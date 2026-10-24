/*
 * Render into an exported linear GBM DMA-BUF; no modeset or master acquisition.
 * Build with pkg-config cflags/libs for gbm egl glesv2 libdrm.
 * Run under an external timeout, for example: timeout 30s ./egl-gbm-smoke card0
 */
#define _POSIX_C_SOURCE 200809L
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <gbm.h>
#include <xf86drm.h>
#include <drm_fourcc.h>
#include <linux/dma-buf.h>

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define SIDE 16u
#define CHECK_GL(operation) do { \
    GLenum checked_error = glGetError(); \
    if (checked_error != GL_NO_ERROR) { \
        fprintf(stderr, "%s failed: GL error=0x%x\n", operation, checked_error); \
        goto cleanup; \
    } \
} while (0)
#define EGL_FAILURE(operation) do { \
    fprintf(stderr, "%s failed: EGL error=0x%x\n", operation, eglGetError()); \
    goto cleanup; \
} while (0)

static int has_extension(const char *extensions, const char *wanted)
{
    if (!extensions) return 0;
    size_t length = strlen(wanted);
    const char *match = extensions;
    while ((match = strstr(match, wanted))) {
        if ((match == extensions || match[-1] == ' ') &&
            (match[length] == '\0' || match[length] == ' ')) return 1;
        match += length;
    }
    return 0;
}

static int contains_ignore_case(const char *text, const char *wanted)
{
    for (; *text; text++) {
        size_t i = 0;
        while (wanted[i] && text[i] &&
               tolower((unsigned char)text[i]) == tolower((unsigned char)wanted[i])) i++;
        if (!wanted[i]) return 1;
    }
    return 0;
}

static int check_rgba(const uint8_t *pixel, uint32_t x, uint32_t y,
                      const char *source, int report)
{
    const uint8_t expected[4] = {(uint8_t)(x * 17u), (uint8_t)(y * 17u),
                                  (uint8_t)((x ^ y) * 17u), 255};
    for (unsigned channel = 0; channel < 4; channel++) {
        int difference = (int)pixel[channel] - expected[channel];
        if (difference < -1 || difference > 1) {
            if (report)
                fprintf(stderr, "%s mismatch: x=%u GL-y=%u channel=%u got=%u expected=%u tolerance=1\n",
                        source, x, y, channel, pixel[channel], expected[channel]);
            return 0;
        }
    }
    return 1;
}

static int check_dma_pattern(const void *mapping, uint32_t offset, uint32_t stride,
                             int flip_y, int report)
{
    for (uint32_t row = 0; row < SIDE; row++) {
        uint32_t gl_y = flip_y ? SIDE - 1u - row : row;
        for (uint32_t x = 0; x < SIDE; x++) {
            const uint8_t *bgra = (const uint8_t *)mapping + offset + (size_t)row * stride + x * 4u;
            uint8_t rgba[4] = {bgra[2], bgra[1], bgra[0], bgra[3]};
            if (!check_rgba(rgba, x, gl_y, flip_y ? "DMA-BUF flipped rows" : "DMA-BUF direct rows", report))
                return 0;
        }
    }
    return 1;
}

int main(int argc, char **argv)
{
    int result = EXIT_FAILURE;
    int drm_fd = -1, dma_fd = -1;
    struct gbm_device *gbm = NULL;
    struct gbm_bo *bo = NULL;
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLContext context = EGL_NO_CONTEXT;
    EGLImageKHR image = EGL_NO_IMAGE_KHR;
    GLuint texture = 0, framebuffer = 0;
    int initialized = 0, current = 0, cpu_access = 0;
    void *cpu_map = MAP_FAILED;
    size_t map_size = 0;
    PFNEGLDESTROYIMAGEKHRPROC destroy_image = NULL;
    if (argc > 2) { fprintf(stderr, "Usage: %s [/dev/dri/card0]\n", argv[0]); return 2; }
    const char *node = argc == 2 ? argv[1] : "/dev/dri/card0";
    setvbuf(stdout, NULL, _IOLBF, 0);
    drm_fd = open(node, O_RDWR | O_CLOEXEC);
    if (drm_fd < 0) { perror(node); goto cleanup; }
    /* First-open primary nodes can grant master implicitly. Relinquish only
     * this FD's implicit ownership; never acquire or steal an existing master. */
    if (drmIsMaster(drm_fd) && drmDropMaster(drm_fd)) {
        perror("drmDropMaster (own implicit master)");
        goto cleanup;
    }
    if (drmIsMaster(drm_fd)) { fputs("Refusing to operate as DRM master\n", stderr); goto cleanup; }
    printf("drmNode=%s drmMaster=no\n", node);
    gbm = gbm_create_device(drm_fd);
    if (!gbm) { perror("gbm_create_device"); goto cleanup; }
    const char *backend = gbm_device_get_backend_name(gbm);
    printf("gbmBackend=%s\n", backend ? backend : "unknown");

    PFNEGLGETPLATFORMDISPLAYEXTPROC platform =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!platform) { fputs("eglGetPlatformDisplayEXT unavailable\n", stderr); goto cleanup; }
    display = platform(EGL_PLATFORM_GBM_KHR, gbm, NULL);
    if (display == EGL_NO_DISPLAY) EGL_FAILURE("GBM EGL display");
    EGLint major = 0, minor = 0;
    if (!eglInitialize(display, &major, &minor)) EGL_FAILURE("eglInitialize");
    initialized = 1;
    const char *egl_extensions = eglQueryString(display, EGL_EXTENSIONS);
    const char *egl_vendor = eglQueryString(display, EGL_VENDOR);
    if (!egl_extensions || !egl_vendor) EGL_FAILURE("eglQueryString");
    printf("eglVersion=%d.%d eglVendor=%s\n", major, minor, egl_vendor);
    if (!has_extension(egl_extensions, "EGL_KHR_surfaceless_context") ||
        !has_extension(egl_extensions, "EGL_KHR_image_base") ||
        !has_extension(egl_extensions, "EGL_EXT_image_dma_buf_import")) {
        fputs("Missing surfaceless-context/image-base/DMA-BUF-import EGL extensions\n", stderr);
        goto cleanup;
    }
    PFNEGLCREATEIMAGEKHRPROC create_image =
        (PFNEGLCREATEIMAGEKHRPROC)eglGetProcAddress("eglCreateImageKHR");
    destroy_image = (PFNEGLDESTROYIMAGEKHRPROC)eglGetProcAddress("eglDestroyImageKHR");
    PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_target =
        (PFNGLEGLIMAGETARGETTEXTURE2DOESPROC)eglGetProcAddress("glEGLImageTargetTexture2DOES");
    if (!create_image || !destroy_image || !image_target) {
        fputs("EGLImage/GLES image entrypoints unavailable\n", stderr); goto cleanup;
    }
    if (!eglBindAPI(EGL_OPENGL_ES_API)) EGL_FAILURE("eglBindAPI");
    const EGLint config_attributes[] = {
        EGL_SURFACE_TYPE, 0, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_NATIVE_VISUAL_ID, (EGLint)GBM_FORMAT_ARGB8888, EGL_NONE,
    };
    EGLConfig config;
    EGLint count = 0;
    if (!eglChooseConfig(display, config_attributes, &config, 1, &count))
        EGL_FAILURE("eglChooseConfig ARGB8888");
    if (!count) { fputs("No ARGB8888 ES2 EGL config\n", stderr); goto cleanup; }
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    if (context == EGL_NO_CONTEXT) EGL_FAILURE("eglCreateContext");
    if (!eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context))
        EGL_FAILURE("eglMakeCurrent surfaceless");
    current = 1;
    const char *gl_renderer = (const char *)glGetString(GL_RENDERER);
    const char *gl_vendor = (const char *)glGetString(GL_VENDOR);
    const char *gl_version = (const char *)glGetString(GL_VERSION);
    const char *gl_extensions = (const char *)glGetString(GL_EXTENSIONS);
    CHECK_GL("glGetString");
    if (!gl_renderer || !gl_vendor || !gl_version || !gl_extensions) {
        fputs("GL identity unavailable\n", stderr); goto cleanup;
    }
    printf("glVendor=%s\nglRenderer=%s\nglVersion=%s\n", gl_vendor, gl_renderer, gl_version);
    if (contains_ignore_case(gl_renderer, "llvmpipe") || contains_ignore_case(gl_renderer, "softpipe") ||
        contains_ignore_case(gl_renderer, "lavapipe") || contains_ignore_case(gl_renderer, "swiftshader")) {
        fputs("Refusing software renderer\n", stderr); goto cleanup;
    }
    if (!has_extension(gl_extensions, "GL_OES_EGL_image")) {
        fputs("GL_OES_EGL_image unavailable\n", stderr); goto cleanup;
    }

    const uint32_t usage = GBM_BO_USE_LINEAR | GBM_BO_USE_RENDERING | GBM_BO_USE_SCANOUT;
    if (!gbm_device_is_format_supported(gbm, GBM_FORMAT_ARGB8888, usage)) {
        fputs("ARGB8888 LINEAR|RENDERING|SCANOUT unsupported\n", stderr); goto cleanup;
    }
    bo = gbm_bo_create(gbm, SIDE, SIDE, GBM_FORMAT_ARGB8888, usage);
    if (!bo) { perror("gbm_bo_create linear scanout"); goto cleanup; }
    uint64_t modifier = gbm_bo_get_modifier(bo);
    uint32_t format = gbm_bo_get_format(bo);
    uint32_t width = gbm_bo_get_width(bo), height = gbm_bo_get_height(bo);
    uint32_t stride = gbm_bo_get_stride_for_plane(bo, 0);
    uint32_t offset = gbm_bo_get_offset(bo, 0);
    int plane_count = gbm_bo_get_plane_count(bo);
    printf("boFormat=0x%08x (ARGB8888) size=%ux%u planes=%d stride=%u offset=%u modifier=0x%016" PRIx64 "\n",
           format, width, height, plane_count, stride, offset, modifier);
    if (width != SIDE || height != SIDE || format != GBM_FORMAT_ARGB8888 ||
        (modifier != DRM_FORMAT_MOD_LINEAR && modifier != DRM_FORMAT_MOD_INVALID) || plane_count != 1 ||
        stride < SIDE * 4u || stride > INT_MAX || offset > INT_MAX ||
        (uint64_t)offset + (uint64_t)stride * SIDE > SIZE_MAX) {
        fputs("BO requires single-plane ARGB8888 with LINEAR or undeclared modifier\n", stderr); goto cleanup;
    }
    printf("declaredLayout=%s requestedUsage=LINEAR|RENDERING|SCANOUT\n",
           modifier == DRM_FORMAT_MOD_LINEAR ? "LINEAR" : "UNDECLARED (DRM_FORMAT_MOD_INVALID)");
    dma_fd = gbm_bo_get_fd_for_plane(bo, 0);
    if (dma_fd < 0) { perror("gbm_bo_get_fd_for_plane"); goto cleanup; }
    EGLint attributes[24] = {
        EGL_WIDTH, SIDE, EGL_HEIGHT, SIDE, EGL_LINUX_DRM_FOURCC_EXT, (EGLint)format,
        EGL_DMA_BUF_PLANE0_FD_EXT, dma_fd, EGL_DMA_BUF_PLANE0_OFFSET_EXT, (EGLint)offset,
        EGL_DMA_BUF_PLANE0_PITCH_EXT, (EGLint)stride,
    };
    unsigned attribute_count = 12;
    if (modifier == DRM_FORMAT_MOD_LINEAR &&
        has_extension(egl_extensions, "EGL_EXT_image_dma_buf_import_modifiers")) {
        attributes[attribute_count++] = EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT;
        attributes[attribute_count++] = 0;
        attributes[attribute_count++] = EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT;
        attributes[attribute_count++] = 0;
        printf("modifierImport=explicit-linear\n");
    } else {
        printf("modifierImport=implicit; CPU XY-pattern check will validate actual row layout\n");
    }
    attributes[attribute_count] = EGL_NONE;
    image = create_image(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, NULL, attributes);
    if (image == EGL_NO_IMAGE_KHR) EGL_FAILURE("eglCreateImageKHR DMA-BUF");
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    image_target(GL_TEXTURE_2D, (GLeglImageOES)image);
    CHECK_GL("EGLImage texture import");
    glGenFramebuffers(1, &framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
    GLenum framebuffer_status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
    CHECK_GL("DMA-BUF framebuffer attachment");
    if (framebuffer_status != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "DMA-BUF framebuffer incomplete: 0x%x\n", framebuffer_status); goto cleanup;
    }
    glViewport(0, 0, SIDE, SIDE);
    glDisable(GL_DITHER);
    glEnable(GL_SCISSOR_TEST);
    /* Unique XY colors reveal tiling, swizzles, stale rows and stride errors. */
    for (uint32_t y = 0; y < SIDE; y++) {
        for (uint32_t x = 0; x < SIDE; x++) {
            glScissor((GLint)x, (GLint)y, 1, 1);
            glClearColor((float)(x * 17u) / 255.0f, (float)(y * 17u) / 255.0f,
                         (float)((x ^ y) * 17u) / 255.0f, 1.0f);
            glClear(GL_COLOR_BUFFER_BIT);
        }
    }
    glDisable(GL_SCISSOR_TEST);
    CHECK_GL("GPU XY-pattern clears");
    uint8_t pixels[SIDE * SIDE * 4u] = {0};
    glReadPixels(0, 0, SIDE, SIDE, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    glFinish();
    CHECK_GL("GPU framebuffer readback/finish");
    for (uint32_t i = 0; i < SIDE * SIDE; i++)
        if (!check_rgba(&pixels[i * 4u], i % SIDE, i / SIDE, "GLES XY pattern", 1)) goto cleanup;
    printf("glReadbackFirstRGBA=%u,%u,%u,%u pixelsChecked=%u tolerance=1 pattern=R:17x,G:17y,B:17(x^y),A:255\n",
           pixels[0], pixels[1], pixels[2], pixels[3], SIDE * SIDE);

    /* Verify the exported storage itself, not only a driver's GL-side copy. */
    struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ};
    if (ioctl(dma_fd, DMA_BUF_IOCTL_SYNC, &sync)) { perror("DMA-BUF CPU READ start"); goto cleanup; }
    cpu_access = 1;
    map_size = (size_t)offset + (size_t)stride * SIDE;
    cpu_map = mmap(NULL, map_size, PROT_READ, MAP_SHARED, dma_fd, 0);
    if (cpu_map == MAP_FAILED) { perror("DMA-BUF mmap"); goto cleanup; }
    int direct_rows = check_dma_pattern(cpu_map, offset, stride, 0, 0);
    int flipped_rows = check_dma_pattern(cpu_map, offset, stride, 1, 0);
    if (!direct_rows && !flipped_rows) {
        fputs("DMA-BUF XY pattern is neither linear rows nor a complete vertical flip\n", stderr);
        check_dma_pattern(cpu_map, offset, stride, 0, 1);
        check_dma_pattern(cpu_map, offset, stride, 1, 1);
        goto cleanup;
    }
    printf("dmaBufRowOrder=%s\nvalidatedLayout=linear rows for this 16x16 allocation\n",
           direct_rows ? "row 0 = GL y=0" : "row 0 = GL y=15 (complete vertical flip)");
    sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
    if (ioctl(dma_fd, DMA_BUF_IOCTL_SYNC, &sync)) { perror("DMA-BUF CPU READ end"); goto cleanup; }
    cpu_access = 0;
    printf("dmaBufPixelsChecked=%u tolerance=1\n", SIDE * SIDE);
    result = EXIT_SUCCESS;

cleanup:
    if (cpu_access) {
        struct dma_buf_sync end = {.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ};
        if (ioctl(dma_fd, DMA_BUF_IOCTL_SYNC, &end)) perror("cleanup DMA-BUF CPU READ end");
    }
    if (cpu_map != MAP_FAILED && munmap(cpu_map, map_size)) {
        perror("munmap DMA-BUF"); result = EXIT_FAILURE;
    }
    if (current) {
        if (framebuffer) glDeleteFramebuffers(1, &framebuffer);
        if (texture) glDeleteTextures(1, &texture);
        GLenum cleanup_error = glGetError();
        if (cleanup_error != GL_NO_ERROR) {
            fprintf(stderr, "GL object cleanup failed: 0x%x\n", cleanup_error); result = EXIT_FAILURE;
        }
    }
    if (image != EGL_NO_IMAGE_KHR && destroy_image && !destroy_image(display, image)) {
        fprintf(stderr, "eglDestroyImageKHR failed: 0x%x\n", eglGetError()); result = EXIT_FAILURE;
    }
    if (current && !eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT)) {
        fprintf(stderr, "release current context failed: 0x%x\n", eglGetError()); result = EXIT_FAILURE;
    }
    if (context != EGL_NO_CONTEXT && !eglDestroyContext(display, context)) {
        fprintf(stderr, "eglDestroyContext failed: 0x%x\n", eglGetError()); result = EXIT_FAILURE;
    }
    if (initialized && !eglTerminate(display)) {
        fprintf(stderr, "eglTerminate failed: 0x%x\n", eglGetError()); result = EXIT_FAILURE;
    }
    if (dma_fd >= 0) close(dma_fd);
    if (bo) gbm_bo_destroy(bo);
    if (gbm) gbm_device_destroy(gbm);
    if (drm_fd >= 0) close(drm_fd);
    if (result == EXIT_SUCCESS) puts("GPU_DMA_BUF_RENDER_READBACK_PASS");
    return result;
}
