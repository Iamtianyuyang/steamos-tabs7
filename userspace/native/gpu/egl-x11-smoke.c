/*
 * Real Xlib/EGL window test: GPU rendering, swap, and X-server pixel readback.
 * cc -O2 -std=c11 -Wall -Wextra -Werror egl-x11-smoke.c \
 *    $(pkg-config --cflags --libs x11 egl glesv2) -o egl-x11-smoke
 * Run with the experimental private environment and an external timeout.
 * This takes no DRM master, changes no output, and starts no compositor.
 */
#define _POSIX_C_SOURCE 200809L
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <ctype.h>
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int x_errors;
static unsigned map_events, configure_events, expose_events;
static int visibility = -1;

static int x_error(Display *display, XErrorEvent *event)
{
    char text[128];
    XGetErrorText(display, event->error_code, text, sizeof(text));
    fprintf(stderr, "X11 error: %s request=%u minor=%u\n", text,
            event->request_code, event->minor_code);
    x_errors++;
    return 0;
}

static int contains(const char *text, const char *word)
{
    if (!text) return 0;
    for (; *text; text++) {
        size_t i = 0;
        while (word[i] && text[i] &&
               tolower((unsigned char)word[i]) == tolower((unsigned char)text[i])) i++;
        if (!word[i]) return 1;
    }
    return 0;
}

static int64_t monotonic_ms(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void drain_events(Display *display, Window window)
{
    while (XPending(display)) {
        XEvent event;
        XNextEvent(display, &event);
        if (event.xany.window != window) continue;
        if (event.type == MapNotify) {
            map_events++;
            fprintf(stderr, "X11 MapNotify count=%u\n", map_events);
        } else if (event.type == ConfigureNotify) {
            configure_events++;
            fprintf(stderr, "X11 ConfigureNotify count=%u geometry=%dx%d+%d+%d\n",
                    configure_events, event.xconfigure.width, event.xconfigure.height,
                    event.xconfigure.x, event.xconfigure.y);
        } else if (event.type == Expose) {
            expose_events++;
            fprintf(stderr, "X11 Expose count=%u rectangle=%dx%d+%d+%d remaining=%d\n",
                    expose_events, event.xexpose.width, event.xexpose.height,
                    event.xexpose.x, event.xexpose.y, event.xexpose.count);
        } else if (event.type == VisibilityNotify) {
            visibility = event.xvisibility.state;
            fprintf(stderr, "X11 VisibilityNotify state=%d (0=unobscured 1=partial 2=full)\n", visibility);
        } else if (event.type == UnmapNotify || event.type == DestroyNotify) {
            fprintf(stderr, "X11 window event=%s\n", event.type == UnmapNotify ? "UnmapNotify" : "DestroyNotify");
        }
    }
}

static void log_window(Display *display, Window window, const char *stage)
{
    XWindowAttributes current;
    int root_x = 0, root_y = 0;
    Window child;
    if (!XGetWindowAttributes(display, window, &current)) return;
    XTranslateCoordinates(display, window, current.root, 0, 0, &root_x, &root_y, &child);
    fprintf(stderr, "X11 %s window=0x%lx geometry=%dx%d root_xy=%d,%d map_state=%d "
            "depth=%d backing_store=%d visibility=%d events(map/configure/expose)=%u/%u/%u\n",
            stage, window, current.width, current.height, root_x, root_y, current.map_state,
            current.depth, current.backing_store, visibility, map_events, configure_events, expose_events);
}

static int wait_mapped(Display *display, Window window)
{
    int64_t deadline = monotonic_ms() + 5000;
    int last_width = -1, last_height = -1;
    int64_t stable_since = monotonic_ms();
    while (monotonic_ms() < deadline) {
        drain_events(display, window);
        XWindowAttributes current;
        if (!XGetWindowAttributes(display, window, &current) || x_errors) return 0;
        if (current.width != last_width || current.height != last_height || current.map_state != IsViewable) {
            last_width = current.width;
            last_height = current.height;
            stable_since = monotonic_ms();
        }
        if (map_events && current.map_state == IsViewable && monotonic_ms() - stable_since >= 200) return 1;
        struct pollfd descriptor = {.fd = ConnectionNumber(display), .events = POLLIN};
        int ready = poll(&descriptor, 1, 20);
        if (ready < 0 && errno != EINTR) return 0;
        if (ready > 0 && descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) return 0;
    }
    return 0;
}

static unsigned channel(unsigned long pixel, unsigned long mask)
{
    if (!mask) return 0;
    while (!(mask & 1)) { pixel >>= 1; mask >>= 1; }
    return (unsigned)(((pixel & mask) * 255 + mask / 2) / mask);
}

static int near(unsigned actual, unsigned expected)
{
    return actual + 1 >= expected && expected + 1 >= actual;
}

#define EGL_CHECK(call) do { \
    if (!(call)) { fprintf(stderr, "%s: EGL error 0x%x\n", #call, eglGetError()); goto cleanup; } \
} while (0)

int main(int argc, char **argv)
{
    int result = EXIT_FAILURE;
    unsigned hold = 3;
    Display *xdisplay = NULL;
    XVisualInfo *visual = NULL;
    Colormap colormap = None;
    Window window = None;
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLContext context = EGL_NO_CONTEXT;
    EGLSurface surface = EGL_NO_SURFACE;
    int initialized = 0;
    int rendered = 0;
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);

    if (argc > 2) { fprintf(stderr, "Usage: %s [hold-seconds: 0..30]\n", argv[0]); return 2; }
    if (argc == 2) {
        char *end;
        errno = 0;
        unsigned long parsed = strtoul(argv[1], &end, 10);
        if (errno || !argv[1][0] || *end || parsed > 30) return 2;
        hold = (unsigned)parsed;
    }
    if (!getenv("DISPLAY") || !getenv("DISPLAY")[0]) {
        fputs("An existing Xwayland DISPLAY is required\n", stderr); return 1;
    }
    XSetErrorHandler(x_error);
    xdisplay = XOpenDisplay(NULL);
    if (!xdisplay) { fputs("XOpenDisplay failed\n", stderr); goto cleanup; }
    printf("DISPLAY=%s X11Vendor=%s\n", DisplayString(xdisplay), ServerVendor(xdisplay));
    fprintf(stderr, "LIBGL_KOPPER_DISABLE=%s SWRAST_NO_PRESENT=%s EGL_PLATFORM=%s\n",
            getenv("LIBGL_KOPPER_DISABLE") ? getenv("LIBGL_KOPPER_DISABLE") : "unset",
            getenv("SWRAST_NO_PRESENT") ? getenv("SWRAST_NO_PRESENT") : "unset",
            getenv("EGL_PLATFORM") ? getenv("EGL_PLATFORM") : "unset");
    PFNEGLGETPLATFORMDISPLAYEXTPROC platform =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!platform) { fputs("EGL platform entrypoint unavailable\n", stderr); goto cleanup; }
    display = platform(EGL_PLATFORM_X11_EXT, xdisplay, NULL);
    if (display == EGL_NO_DISPLAY) { fprintf(stderr, "EGL X11 display unavailable: 0x%x\n", eglGetError()); goto cleanup; }
    EGLint major, minor;
    EGL_CHECK(eglInitialize(display, &major, &minor));
    initialized = 1;
    printf("EGL=%d.%d vendor=%s\n", major, minor, eglQueryString(display, EGL_VENDOR));
    const EGLint attributes[] = {EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_NONE};
    EGLConfig config;
    EGLint count, visual_id;
    EGL_CHECK(eglChooseConfig(display, attributes, &config, 1, &count));
    if (!count) { fputs("No X11 ES2 window config\n", stderr); goto cleanup; }
    EGL_CHECK(eglGetConfigAttrib(display, config, EGL_NATIVE_VISUAL_ID, &visual_id));
    XVisualInfo template = {.visualid = (VisualID)visual_id, .screen = DefaultScreen(xdisplay)};
    int visual_count;
    visual = XGetVisualInfo(xdisplay, VisualIDMask | VisualScreenMask, &template, &visual_count);
    if (!visual || !visual_count || visual->class != TrueColor) {
        fputs("No matching TrueColor X11 visual\n", stderr); goto cleanup;
    }
    colormap = XCreateColormap(xdisplay, RootWindow(xdisplay, visual->screen), visual->visual, AllocNone);
    XSetWindowAttributes settings = {.colormap = colormap,
        .event_mask = ExposureMask | StructureNotifyMask | VisibilityChangeMask};
    window = XCreateWindow(xdisplay, RootWindow(xdisplay, visual->screen),
        0, 0, 320, 240, 0, visual->depth, InputOutput, visual->visual,
        CWColormap | CWEventMask, &settings);
    if (window == None) { fputs("XCreateWindow failed\n", stderr); goto cleanup; }
    XStoreName(xdisplay, window, "Tab S7 EGL X11 GPU test");
    XClassHint class_hint = {.res_name = "tabs7-egl-x11-smoke", .res_class = "Tabs7EglX11Smoke"};
    XSetClassHint(xdisplay, window, &class_hint);
    XMapRaised(xdisplay, window);
    XSync(xdisplay, False);
    if (x_errors || !wait_mapped(xdisplay, window)) {
        fputs("X11 window was not mapped within 5 seconds\n", stderr); goto cleanup;
    }
    log_window(xdisplay, window, "mapped-stable");
    EGL_CHECK(eglBindAPI(EGL_OPENGL_ES_API));
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    if (context == EGL_NO_CONTEXT) { fprintf(stderr, "eglCreateContext: 0x%x\n", eglGetError()); goto cleanup; }
    surface = eglCreateWindowSurface(display, config, (EGLNativeWindowType)window, NULL);
    if (surface == EGL_NO_SURFACE) { fprintf(stderr, "eglCreateWindowSurface: 0x%x\n", eglGetError()); goto cleanup; }
    EGL_CHECK(eglMakeCurrent(display, surface, surface, context));
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    const char *version = (const char *)glGetString(GL_VERSION);
    printf("GLRenderer=%s\nGLVersion=%s\n", renderer ? renderer : "NULL", version ? version : "NULL");
    if (!contains(renderer, "zink") || !contains(renderer, "turnip") || !contains(renderer, "650") ||
        contains(renderer, "llvmpipe") || contains(renderer, "softpipe") ||
        contains(renderer, "lavapipe") || contains(renderer, "swiftshader")) {
        fputs("Expected real Zink/Turnip Adreno 650; refusing software renderer\n", stderr); goto cleanup;
    }
    /* Phoc may maximize an Xwayland window only after its first buffer.
     * This swap activates the window; it does not count as a checked frame.
     */
    glDisable(GL_SCISSOR_TEST);
    glClearColor(0.1f, 0.1f, 0.1f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    if (glGetError() != GL_NO_ERROR) { fputs("Warmup clear failed\n", stderr); goto cleanup; }
    rendered = 1;
    EGL_CHECK(eglSwapBuffers(display, surface));
    XSync(xdisplay, False);
    if (x_errors || !wait_mapped(xdisplay, window)) {
        fputs("Window did not settle after its activation swap\n", stderr); goto cleanup;
    }
    log_window(xdisplay, window, "activation-stable");
    unsigned frame = 0, geometry_restarts = 0;
    EGLint checked_width = 0, checked_height = 0;
    int64_t frames_deadline = monotonic_ms() + 10000;
    while (frame < 3) {
        if (monotonic_ms() >= frames_deadline) {
            fputs("Three checked frames did not finish within 10 seconds\n", stderr); goto cleanup;
        }
        EGLint width, height;
        EGL_CHECK(eglQuerySurface(display, surface, EGL_WIDTH, &width));
        EGL_CHECK(eglQuerySurface(display, surface, EGL_HEIGHT, &height));
        if (width < 4 || height < 4 || width > 8192 || height > 8192) {
            fputs("Unexpected EGL window dimensions\n", stderr); goto cleanup;
        }
        if (frame && (width != checked_width || height != checked_height)) {
            fprintf(stderr, "Geometry changed between frames from %dx%d to %dx%d; restarting all three checks\n",
                    checked_width, checked_height, width, height);
            if (++geometry_restarts > 4 || !wait_mapped(xdisplay, window)) {
                fputs("Window geometry did not settle within four restarts\n", stderr); goto cleanup;
            }
            frame = 0;
            continue;
        }
        glViewport(0, 0, width, height);
        glEnable(GL_SCISSOR_TEST);
        unsigned colors[4][3];
        GLint positions[4][2];
        for (unsigned tile = 0; tile < 4; tile++) {
            GLint x = (tile & 1) ? width / 2 : 0;
            GLint y = (tile & 2) ? height / 2 : 0;
            GLsizei w = (tile & 1) ? width - x : width / 2;
            GLsizei h = (tile & 2) ? height - y : height / 2;
            colors[tile][0] = 24 + frame * 35 + tile * 23;
            colors[tile][1] = 180 - frame * 19 - tile * 31;
            colors[tile][2] = 40 + frame * 27 + tile * 41;
            positions[tile][0] = x + w / 2;
            positions[tile][1] = y + h / 2;
            glScissor(x, y, w, h);
            glClearColor(colors[tile][0] / 255.0f, colors[tile][1] / 255.0f,
                         colors[tile][2] / 255.0f, 1.0f);
            glClear(GL_COLOR_BUFFER_BIT);
        }
        glDisable(GL_SCISSOR_TEST);
        for (unsigned tile = 0; tile < 4; tile++) {
            uint8_t pixel[4] = {0};
            glReadPixels(positions[tile][0], positions[tile][1], 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
            GLenum read_error = glGetError();
            if (read_error != GL_NO_ERROR) {
                fprintf(stderr, "GL render/readback error frame=%u tile=%u code=0x%x\n", frame, tile, read_error);
                goto cleanup;
            }
            for (unsigned component = 0; component < 3; component++) {
                if (!near(pixel[component], colors[tile][component])) {
                    fprintf(stderr, "GPU readback mismatch frame=%u tile=%u channel=%u got=%u expected=%u\n",
                            frame, tile, component, pixel[component], colors[tile][component]); goto cleanup;
                }
            }
        }
        GLenum error = glGetError();
        if (error != GL_NO_ERROR) { fprintf(stderr, "GL render/readback error 0x%x\n", error); goto cleanup; }
        rendered = 1;
        printf("frame=%u GPUReadback=PASS surface=%dx%d\n", frame, width, height);
        EGL_CHECK(eglSwapBuffers(display, surface));
        int matched = 0;
        int geometry_changed = 0;
        int64_t present_start = monotonic_ms();
        unsigned samples = 0;
        unsigned rgb[4][3] = {{0}};
        while (monotonic_ms() - present_start < 1000) {
            XSync(xdisplay, False);
            drain_events(xdisplay, window);
            XWindowAttributes current;
            if (!XGetWindowAttributes(xdisplay, window, &current) || current.map_state != IsViewable || x_errors) {
                fputs("X11 window became unavailable during presentation\n", stderr); goto cleanup;
            }
            if (current.width != width || current.height != height) {
                log_window(xdisplay, window, "geometry-changed");
                geometry_changed = 1;
                break;
            }
            matched = 1;
            for (unsigned tile = 0; tile < 4; tile++) {
                XImage *image = XGetImage(xdisplay, window, positions[tile][0],
                    height - 1 - positions[tile][1], 1, 1, AllPlanes, ZPixmap);
                if (!image || x_errors) {
                    if (image) XDestroyImage(image);
                    fputs("X-server window readback failed after swap\n", stderr); goto cleanup;
                }
                unsigned long pixel = XGetPixel(image, 0, 0);
                rgb[tile][0] = channel(pixel, visual->red_mask);
                rgb[tile][1] = channel(pixel, visual->green_mask);
                rgb[tile][2] = channel(pixel, visual->blue_mask);
                XDestroyImage(image);
                for (unsigned component = 0; component < 3; component++)
                    if (!near(rgb[tile][component], colors[tile][component])) matched = 0;
            }
            samples++;
            if (matched) break;
            struct timespec delay = {.tv_nsec = 20000000};
            nanosleep(&delay, NULL);
        }
        if (geometry_changed) {
            if (++geometry_restarts > 4 || !wait_mapped(xdisplay, window)) {
                fputs("Window geometry did not settle within four restarts\n", stderr); goto cleanup;
            }
            fprintf(stderr, "Presentation geometry changed; restarting all three checked frames (restart=%u)\n", geometry_restarts);
            frame = 0;
            continue;
        }
        log_window(xdisplay, window, matched ? "presented" : "presentation-mismatch");
        fprintf(stderr, "X11 frame=%u presentation_samples=%u elapsed_ms=%lld\n",
                frame, samples, (long long)(monotonic_ms() - present_start));
        for (unsigned tile = 0; tile < 4; tile++) {
            fprintf(stderr, "X11 frame=%u tile=%u sample_xy=%d,%d actual_rgb=%u,%u,%u expected_rgb=%u,%u,%u\n",
                    frame, tile, positions[tile][0], height - 1 - positions[tile][1],
                    rgb[tile][0], rgb[tile][1], rgb[tile][2], colors[tile][0], colors[tile][1], colors[tile][2]);
        }
        if (!matched) {
            fputs("X11 presented pixels did not match within 1 second; FAIL\n", stderr);
            goto cleanup;
        }
        printf("frame=%u surface=%dx%d GPU+X11PixelsChecked=4 swap=PASS\n", frame, width, height);
        checked_width = width;
        checked_height = height;
        frame++;
        /* Give asynchronous WM configuration a chance to arrive between
         * checks, including after frame 2 before declaring success.
         */
        struct timespec frame_delay = {.tv_nsec = 100000000};
        nanosleep(&frame_delay, NULL);
        XSync(xdisplay, False);
        drain_events(xdisplay, window);
        XWindowAttributes after_frame;
        if (!XGetWindowAttributes(xdisplay, window, &after_frame) ||
            after_frame.map_state != IsViewable || x_errors) {
            fputs("X11 window became unavailable after the checked frame\n", stderr); goto cleanup;
        }
        if (after_frame.width != width || after_frame.height != height) {
            if (++geometry_restarts > 4 || !wait_mapped(xdisplay, window)) {
                fputs("Window geometry did not settle within four restarts\n", stderr); goto cleanup;
            }
            fprintf(stderr, "Post-frame geometry changed; restarting all three checked frames (restart=%u)\n", geometry_restarts);
            frame = 0;
        }
    }
    printf("FinalGeometry=%dx%d consecutive_checked_frames=3 geometry_restarts=%u\n",
           checked_width, checked_height, geometry_restarts);
    fflush(stdout);
    if (hold) { struct timespec duration = {.tv_sec = hold}; nanosleep(&duration, NULL); }
    result = EXIT_SUCCESS;
cleanup:
    if (result != EXIT_SUCCESS && rendered && xdisplay && window != None) {
        unsigned failure_hold = hold < 3 ? 3 : hold;
        fprintf(stderr, "Keeping failed window 0x%lx mapped for %u seconds for compositor screenshot\n", window, failure_hold);
        struct timespec duration = {.tv_sec = failure_hold};
        nanosleep(&duration, NULL);
    }
    if (initialized) {
        eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (surface != EGL_NO_SURFACE) eglDestroySurface(display, surface);
        if (context != EGL_NO_CONTEXT) eglDestroyContext(display, context);
        eglTerminate(display);
    }
    if (xdisplay) {
        if (window != None) XDestroyWindow(xdisplay, window);
        if (colormap != None) XFreeColormap(xdisplay, colormap);
        if (visual) XFree(visual);
        XSync(xdisplay, False);
        XCloseDisplay(xdisplay);
    }
    if (result == EXIT_SUCCESS && !x_errors) puts("GPU_EGL_X11_WINDOW_SWAP_READBACK_PASS");
    else result = EXIT_FAILURE;
    return result;
}
