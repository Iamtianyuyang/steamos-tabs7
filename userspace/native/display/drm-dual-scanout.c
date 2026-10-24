/* SPDX-License-Identifier: MIT
 * Standard atomic KMS control for SM-T870, matching QTI minui's two half planes.
 * Build: cc -std=c11 -O2 -Wall -Wextra -Werror drm-dual-scanout.c \
 *           $(pkg-config --cflags --libs libdrm) -o drm-dual-scanout
 * Stop the current display owner first. Existing active bindings are refused.
 * Exit on INT/TERM/HUP: atomic disable, then release only our FB/GEM/blob.
 * No MMIO, vendor ioctl, SDE blob, dirtyfb or cache-maintenance ioctl is used.
 */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>

enum { WIDTH = 1600, HEIGHT = 2560, PITCH = WIDTH * 4 };
struct object {
    uint32_t id;
    drmModeObjectProperties *props;
    drmModePropertyRes **info;
};
static volatile sig_atomic_t stop_signal;
static void stop(int number) { stop_signal = number; }

static void usage(FILE *out, const char *program)
{
    fprintf(out, "Usage: %s /dev/dri/cardN --connector ID --crtc ID\n"
            "       --planes LEFT,RIGHT --mode-index N [--single]\n"
            "IDs are explicit; mode index is zero based. Only connected DSI,\n"
            "1600x2560 and XR24 are accepted. --single uses LEFT at full width\n"
            "and disables RIGHT. Stop the existing DRM owner before running.\n"
            "No active binding is replaced. Exit leaves this output disabled.\n",
            program);
}

static bool number(const char *text, uint32_t *value, bool allow_zero)
{
    char *end;
    if (!text[0] || text[0] < '0' || text[0] > '9') return false;
    errno = 0;
    unsigned long long parsed = strtoull(text, &end, 0);
    if (errno || *end || parsed > UINT32_MAX || (!allow_zero && !parsed)) return false;
    *value = (uint32_t)parsed;
    return true;
}

static void object_free(struct object *obj)
{
    if (obj->info && obj->props)
        for (uint32_t i = 0; i < obj->props->count_props; ++i)
            if (obj->info[i]) drmModeFreeProperty(obj->info[i]);
    free(obj->info);
    if (obj->props) drmModeFreeObjectProperties(obj->props);
}

static bool object_get(int fd, struct object *obj, uint32_t id, uint32_t type)
{
    obj->id = id;
    obj->props = drmModeObjectGetProperties(fd, id, type);
    if (!obj->props) return false;
    obj->info = calloc(obj->props->count_props, sizeof(*obj->info));
    if (!obj->info) return false;
    for (uint32_t i = 0; i < obj->props->count_props; ++i) {
        obj->info[i] = drmModeGetProperty(fd, obj->props->props[i]);
        if (!obj->info[i]) return false;
    }
    return true;
}

static drmModePropertyRes *property(struct object *obj, const char *name, uint64_t *value)
{
    for (uint32_t i = 0; i < obj->props->count_props; ++i)
        if (!strcmp(obj->info[i]->name, name)) {
            if (value) *value = obj->props->prop_values[i];
            return obj->info[i];
        }
    fprintf(stderr, "Object %u lacks required property %s.\n", obj->id, name);
    return NULL;
}

static bool add(drmModeAtomicReq *req, struct object *obj, const char *name, uint64_t value)
{
    drmModePropertyRes *prop = property(obj, name, NULL);
    if (!prop) return false;
    if (drmModeAtomicAddProperty(req, obj->id, prop->prop_id, value) < 0) {
        fprintf(stderr, "Adding object %u %s: %s\n", obj->id, name, strerror(errno));
        return false;
    }
    return true;
}

static bool plane_set(drmModeAtomicReq *req, struct object *obj, uint32_t crtc,
                      uint32_t fb, uint32_t x, uint32_t width)
{
    if (!add(req, obj, "FB_ID", fb) || !add(req, obj, "CRTC_ID", crtc)) return false;
    if (!fb) return true;
    return add(req, obj, "SRC_X", (uint64_t)x << 16) && add(req, obj, "SRC_Y", 0) &&
        add(req, obj, "SRC_W", (uint64_t)width << 16) &&
        add(req, obj, "SRC_H", (uint64_t)HEIGHT << 16) &&
        add(req, obj, "CRTC_X", x) && add(req, obj, "CRTC_Y", 0) &&
        add(req, obj, "CRTC_W", width) && add(req, obj, "CRTC_H", HEIGHT) &&
        add(req, obj, "zpos", 0);
}

static int commit(int fd, struct object *conn, struct object *crtc, struct object plane[2],
                  uint32_t fb, uint32_t blob, bool single, bool test)
{
    drmModeAtomicReq *req = drmModeAtomicAlloc();
    if (!req) return -1;
    bool ready = add(req, conn, "CRTC_ID", fb ? crtc->id : 0) &&
        add(req, crtc, "MODE_ID", fb ? blob : 0) && add(req, crtc, "ACTIVE", !!fb) &&
        plane_set(req, &plane[0], fb ? crtc->id : 0, fb, 0, single ? WIDTH : WIDTH / 2) &&
        plane_set(req, &plane[1], fb && !single ? crtc->id : 0,
                  single ? 0 : fb, WIDTH / 2, WIDTH / 2);
    int result = ready ? drmModeAtomicCommit(fd, req, DRM_MODE_ATOMIC_ALLOW_MODESET |
                                             (test ? DRM_MODE_ATOMIC_TEST_ONLY : 0), NULL) : -1;
    drmModeAtomicFree(req);
    return result;
}

static void draw(uint8_t *buffer)
{
    static const uint32_t colors[2][4] = {
        {0xe04020, 0xe0b020, 0x20c0c0, 0x40b040},
        {0x40b040, 0xc040c0, 0xe0b020, 0xe04020}
    };
    for (unsigned y = 0; y < HEIGHT; ++y) {
        uint32_t *row = (uint32_t *)(buffer + (size_t)y * PITCH);
        for (unsigned x = 0; x < WIDTH; ++x) {
            uint32_t rgb = colors[x >= WIDTH / 2][y / (HEIGHT / 4)];
            if (x % 64 < 2 || y % 64 < 2) rgb = 0x202020;
            if (x < 8 || x >= WIDTH - 8 || y < 8 || y >= HEIGHT - 8 ||
                (x >= WIDTH / 2 - 3 && x <= WIDTH / 2 + 2)) rgb = 0xffffff;
            row[x] = 0xff000000 | rgb;
        }
    }
}

int main(int argc, char **argv)
{
    const char *device = NULL;
    uint32_t connector = 0, crtc_id = 0, ids[2] = {0}, mode_index = UINT32_MAX;
    bool single = false, attempted = false, disable_ok = true;
    int fd = -1, result = 1, crtc_index = -1;
    drmModeRes *resources = NULL;
    drmModeConnector *conn = NULL;
    drmModePlaneRes *planes = NULL;
    struct object conn_obj = {0}, crtc_obj = {0}, plane_obj[2] = {{0}};
    struct drm_mode_create_dumb dumb = {.width = WIDTH, .height = HEIGHT, .bpp = 32};
    uint32_t fb = 0, blob = 0;
    uint8_t *pixels = MAP_FAILED;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--help")) { usage(stdout, argv[0]); return 0; }
        if (!strcmp(argv[i], "--single")) { single = true; continue; }
        if (!strcmp(argv[i], "--planes") && i + 1 < argc) {
            char *text = strdup(argv[++i]), *comma = text ? strchr(text, ',') : NULL;
            bool valid = false;
            if (comma) { *comma = 0; valid = number(text, &ids[0], false) &&
                number(comma + 1, &ids[1], false) && ids[0] != ids[1]; }
            free(text);
            if (!valid) { usage(stderr, argv[0]); return 2; }
        } else if ((!strcmp(argv[i], "--connector") || !strcmp(argv[i], "--crtc") ||
                    !strcmp(argv[i], "--mode-index")) && i + 1 < argc) {
            bool mode = !strcmp(argv[i], "--mode-index");
            uint32_t *target = mode ? &mode_index :
                (!strcmp(argv[i], "--crtc") ? &crtc_id : &connector);
            if (!number(argv[++i], target, mode)) { usage(stderr, argv[0]); return 2; }
        } else if (!device && argv[i][0] != '-') device = argv[i];
        else { usage(stderr, argv[0]); return 2; }
    }
    if (!device || !connector || !crtc_id || !ids[0] || !ids[1] || mode_index == UINT32_MAX) {
        usage(stderr, argv[0]); return 2;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    fd = open(device, O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("Opening DRM device"); goto done; }
    if (!drmIsMaster(fd) && drmSetMaster(fd) != 0) {
        fprintf(stderr, "DRM master unavailable: %s; stop the current owner first.\n", strerror(errno));
        goto done;
    }
    drmVersion *version = drmGetVersion(fd);
    bool msm = version && version->name &&
        (!strcmp(version->name, "msm_drm") || !strcmp(version->name, "msm"));
    if (version) drmFreeVersion(version);
    uint64_t cap = 0;
    if (!msm || drmGetCap(fd, DRM_CAP_DUMB_BUFFER, &cap) || !cap ||
        drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) ||
        drmSetClientCap(fd, DRM_CLIENT_CAP_ATOMIC, 1)) {
        fprintf(stderr, "Requires msm DRM with dumb buffers and atomic/universal planes.\n"); goto done;
    }
    resources = drmModeGetResources(fd);
    conn = drmModeGetConnector(fd, connector);
    planes = drmModeGetPlaneResources(fd);
    if (!resources || !conn || !planes) { perror("Querying KMS resources"); goto done; }
    for (int i = 0; i < resources->count_crtcs; ++i)
        if (resources->crtcs[i] == crtc_id) crtc_index = i;
    if (crtc_index < 0 || crtc_index >= 32 || conn->connector_type != DRM_MODE_CONNECTOR_DSI ||
        conn->connection != DRM_MODE_CONNECTED || mode_index >= (uint32_t)conn->count_modes) {
        fprintf(stderr, "Invalid CRTC, disconnected/non-DSI connector, or mode index.\n"); goto done;
    }
    bool compatible = false;
    for (int i = 0; i < conn->count_encoders; ++i) {
        drmModeEncoder *encoder = drmModeGetEncoder(fd, conn->encoders[i]);
        if (encoder && (encoder->possible_crtcs & (1u << crtc_index))) compatible = true;
        if (encoder) drmModeFreeEncoder(encoder);
    }
    drmModeModeInfo *mode = &conn->modes[mode_index];
    if (!compatible || mode->hdisplay != WIDTH || mode->vdisplay != HEIGHT || !mode->clock ||
        (mode->vrefresh != 48 && mode->vrefresh != 60 && mode->vrefresh != 96 && mode->vrefresh != 120) ||
        (mode->flags & (DRM_MODE_FLAG_INTERLACE | DRM_MODE_FLAG_DBLSCAN))) {
        fprintf(stderr, "Requires compatible connector/CRTC and a listed 1600x2560 panel mode.\n"); goto done;
    }
    if (!object_get(fd, &conn_obj, connector, DRM_MODE_OBJECT_CONNECTOR) ||
        !object_get(fd, &crtc_obj, crtc_id, DRM_MODE_OBJECT_CRTC)) goto query_error;
    uint64_t binding = 0, active = 0, old_mode = 0;
    if (!property(&conn_obj, "CRTC_ID", &binding) || !property(&crtc_obj, "ACTIVE", &active) ||
        !property(&crtc_obj, "MODE_ID", &old_mode)) goto done;
    printf("Existing connector %u CRTC_ID=%" PRIu64 "; CRTC %u ACTIVE=%" PRIu64
           " MODE_ID=%" PRIu64 " (MODE_ID alone does not block)\n",
           connector, binding, crtc_id, active, old_mode);
    bool occupied = binding || active;
    for (int i = 0; i < resources->count_connectors; ++i) {
        if (resources->connectors[i] == connector) continue;
        struct object other = {0};
        uint64_t other_binding = 0;
        bool queried = object_get(fd, &other, resources->connectors[i], DRM_MODE_OBJECT_CONNECTOR) &&
            property(&other, "CRTC_ID", &other_binding);
        if (queried && other_binding) {
            fprintf(stderr, "Existing connector %u CRTC_ID=%" PRIu64 "\n", other.id, other_binding);
            occupied = true;
        }
        object_free(&other);
        if (!queried) goto query_error;
    }
    for (uint32_t i = 0; i < planes->count_planes; ++i) {
        drmModePlane *plane = drmModeGetPlane(fd, planes->planes[i]);
        if (!plane) goto query_error;
        if (plane->crtc_id && plane->fb_id) {
            fprintf(stderr, "Existing active plane %u CRTC_ID=%u FB_ID=%u\n",
                    plane->plane_id, plane->crtc_id, plane->fb_id);
            occupied = true;
        }
        drmModeFreePlane(plane);
    }
    if (occupied) {
        fprintf(stderr, "Refusing active KMS bindings; no buffer allocation or modeset performed.\n"); goto done;
    }
    for (unsigned i = 0; i < 2; ++i) {
        drmModePlane *plane = drmModeGetPlane(fd, ids[i]);
        bool format = false;
        if (plane) for (uint32_t f = 0; f < plane->count_formats; ++f)
            if (plane->formats[f] == DRM_FORMAT_XRGB8888) format = true;
        bool valid = plane && format && (plane->possible_crtcs & (1u << crtc_index));
        if (plane) drmModeFreePlane(plane);
        if (!valid) { fprintf(stderr, "Plane %u lacks XR24 or target CRTC compatibility.\n", ids[i]); goto done; }
        if (!object_get(fd, &plane_obj[i], ids[i], DRM_MODE_OBJECT_PLANE)) goto query_error;
        drmModePropertyRes *zpos = property(&plane_obj[i], "zpos", NULL);
        if (!zpos || !(zpos->flags & DRM_MODE_PROP_RANGE) || zpos->count_values != 2 ||
            zpos->values[0] != 0 || (zpos->flags & DRM_MODE_PROP_IMMUTABLE)) {
            fprintf(stderr, "Plane %u needs writable zpos supporting zero.\n", ids[i]); goto done;
        }
    }
    struct sigaction action = {.sa_handler = stop};
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL) ||
        sigaction(SIGHUP, &action, NULL)) { perror("Installing signal handlers"); goto done; }
    if (drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &dumb)) { perror("CREATE_DUMB"); goto done; }
    if (dumb.pitch != PITCH || dumb.size < (uint64_t)PITCH * HEIGHT || dumb.size > 64 * 1024 * 1024) {
        fprintf(stderr, "Unexpected dumb layout: pitch=%u size=%" PRIu64 "\n", dumb.pitch, (uint64_t)dumb.size); goto done;
    }
    struct drm_mode_map_dumb map = {.handle = dumb.handle};
    if (drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map) || map.offset > INT64_MAX) { perror("MAP_DUMB"); goto done; }
    pixels = mmap(NULL, (size_t)dumb.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)map.offset);
    if (pixels == MAP_FAILED) { perror("mmap"); goto done; }
    draw(pixels);
    uint32_t handles[4] = {dumb.handle}, pitches[4] = {dumb.pitch}, offsets[4] = {0};
    if (drmModeAddFB2(fd, WIDTH, HEIGHT, DRM_FORMAT_XRGB8888, handles, pitches, offsets, &fb, 0) ||
        drmModeCreatePropertyBlob(fd, mode, sizeof(*mode), &blob)) { perror("Creating FB/mode blob"); goto done; }
    printf("Test %s: connector=%u crtc=%u planes=%u,%u mode[%u]=%s refresh=%u clock=%u flags=0x%x\n",
           single ? "single" : "dual", connector, crtc_id, ids[0], ids[1], mode_index,
           mode->name, mode->vrefresh, mode->clock, mode->flags);
    printf("Mode timing: h=%u/%u/%u/%u v=%u/%u/%u/%u; mode copied unchanged.\n",
           mode->hdisplay, mode->hsync_start, mode->hsync_end, mode->htotal,
           mode->vdisplay, mode->vsync_start, mode->vsync_end, mode->vtotal);
    printf("One XR24 FB=%u handle=%u %ux%u pitch=%u size=%" PRIu64
           "; grid64, white border/center; top-to-bottom left R/Y/C/G, right G/M/Y/R.\n",
           fb, dumb.handle, WIDTH, HEIGHT, dumb.pitch, (uint64_t)dumb.size);
    printf("Plane %u: SRC/CRTC x=0 width=%u height=%u zpos=0; plane %u: %s\n",
           ids[0], single ? WIDTH : WIDTH / 2, HEIGHT, ids[1],
           single ? "disabled" : "SRC/CRTC x=800 width=800 height=2560 zpos=0, same FB");
    if (commit(fd, &conn_obj, &crtc_obj, plane_obj, fb, blob, single, true)) { perror("Atomic TEST_ONLY"); goto done; }
    if (stop_signal) goto done;
    attempted = true;
    if (commit(fd, &conn_obj, &crtc_obj, plane_obj, fb, blob, single, false)) { perror("Atomic modeset"); goto done; }
    puts("Scanout active; waiting for SIGINT/SIGTERM/SIGHUP. Exit disables output.");
    sigset_t mask, previous;
    sigemptyset(&mask); sigaddset(&mask, SIGINT); sigaddset(&mask, SIGTERM); sigaddset(&mask, SIGHUP);
    if (sigprocmask(SIG_BLOCK, &mask, &previous)) { perror("Blocking signals"); goto done; }
    sigset_t waiting = previous;
    sigdelset(&waiting, SIGINT); sigdelset(&waiting, SIGTERM); sigdelset(&waiting, SIGHUP);
    while (!stop_signal) sigsuspend(&waiting);
    sigprocmask(SIG_SETMASK, &previous, NULL);
    printf("Stopping after signal %d.\n", stop_signal);
    result = 0;
    goto done;
query_error:
    fprintf(stderr, "Querying DRM object: %s\n", strerror(errno));
done:
    if (attempted && commit(fd, &conn_obj, &crtc_obj, plane_obj, 0, 0, single, false)) {
        fprintf(stderr, "Atomic disable failed: %s; skipping explicit FB/GEM destruction.\n", strerror(errno));
        disable_ok = false; result = 1;
    }
    if (pixels != MAP_FAILED) munmap(pixels, (size_t)dumb.size);
    if (disable_ok && fb && drmModeRmFB(fd, fb)) { perror("Removing own FB"); result = 1; }
    if (blob && drmModeDestroyPropertyBlob(fd, blob)) { perror("Removing own mode blob"); result = 1; }
    if (disable_ok && dumb.handle) {
        struct drm_mode_destroy_dumb destroy = {.handle = dumb.handle};
        if (drmIoctl(fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy)) { perror("Destroying own dumb buffer"); result = 1; }
    }
    object_free(&conn_obj); object_free(&crtc_obj);
    object_free(&plane_obj[0]); object_free(&plane_obj[1]);
    if (planes) drmModeFreePlaneResources(planes);
    if (conn) drmModeFreeConnector(conn);
    if (resources) drmModeFreeResources(resources);
    if (fd >= 0) close(fd);
    return result;
}
