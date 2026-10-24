/* SPDX-License-Identifier: MIT
 * Read-only KMS inspection, compatible with the legacy Linux 4.19 GETFB ABI.
 * Build: cc -std=c11 -O2 -Wall -Wextra -Werror drm-scanout.c \
 *           $(pkg-config --cflags --libs libdrm) -o drm-scanout
 * Usage: ./drm-scanout /dev/dri/card0 [--legacy-client] [--plane ID | --fb ID]
 *        [--dump frame.ppm]
 *
 * No DRM master acquisition, modeset, property write, atomic commit, framebuffer
 * creation, dirtyfb, or cache/synchronization ioctl is issued. SET_CLIENT_CAP
 * only enables this fd's universal-plane and atomic-property query view.
 * GETFB may create an fd-local GEM handle; closing our fd releases it.
 *
 * Primary ABI references:
 * https://github.com/torvalds/linux/blob/v4.19/include/uapi/drm/drm_mode.h
 * https://github.com/torvalds/linux/blob/v4.19/drivers/gpu/drm/drm_framebuffer.c
 * https://github.com/torvalds/linux/blob/v4.19/drivers/gpu/drm/drm_plane.c
 * https://github.com/torvalds/linux/blob/v4.19/drivers/gpu/drm/drm_mode_object.c
 * https://github.com/torvalds/linux/blob/v5.9/drivers/gpu/drm/drm_framebuffer.c
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

struct pixel_stats {
    uint64_t pixels, nonblack, byte_zero, byte_ff, byte_sum;
    unsigned byte_min, byte_max;
};

static void usage(FILE *out, const char *program)
{
    fprintf(out, "Usage: %s /dev/dri/cardN [--legacy-client] [--plane ID | --fb ID]\n"
            "       [--dump NEW.ppm]\n"
            "Queries only. --dump requires one active plane, or an explicit ID.\n"
            "--legacy-client keeps UNIVERSAL_PLANES but does not enable ATOMIC.\n"
            "--fb queries that FB directly, bypassing plane/CRTC enumeration.\n"
            "GETFB2 is attempted for format/modifier/offset; legacy GETFB is fallback.\n"
            "Legacy dumps assume linear little-endian XRGB/ARGB8888 at offset=0.\n"
            "GETFB2 dumps support linear XRGB/ARGB/XBGR/ABGR8888 only.\n"
            "Requires root/CAP_SYS_ADMIN for GETFB's GEM handle while a\n"
            "compositor owns DRM master. Existing output files are refused.\n",
            program);
}

static bool parse_id(const char *text, uint32_t *id)
{
    char *end;
    unsigned long long value;
    if (!text[0] || text[0] == '-')
        return false;
    errno = 0;
    value = strtoull(text, &end, 0);
    if (errno || *end || value == 0 || value > UINT32_MAX)
        return false;
    *id = (uint32_t)value;
    return true;
}

static const char *connection_name(drmModeConnection state)
{
    switch (state) {
    case DRM_MODE_CONNECTED: return "connected";
    case DRM_MODE_DISCONNECTED: return "disconnected";
    default: return "unknown";
    }
}

static bool interesting_property(const char *name)
{
    return !strncmp(name, "SRC_", 4) || !strncmp(name, "CRTC_", 5) ||
           !strcmp(name, "FB_ID") || !strcmp(name, "ACTIVE") ||
           !strcmp(name, "MODE_ID") || !strcmp(name, "type") ||
           !strcasecmp(name, "rotation") || strcasestr(name, "alpha") ||
           !strcasecmp(name, "zpos") || strcasestr(name, "blend");
}

static void print_properties(int fd, uint32_t object, uint32_t type)
{
    drmModeObjectProperties *properties = drmModeObjectGetProperties(fd, object, type);
    if (!properties) {
        printf("  properties unavailable: %s\n", strerror(errno));
        return;
    }
    for (uint32_t i = 0; i < properties->count_props; ++i) {
        drmModePropertyRes *property = drmModeGetProperty(fd, properties->props[i]);
        uint64_t value = properties->prop_values[i];
        if (!property) {
            printf("  property %u unavailable: %s\n", properties->props[i], strerror(errno));
            continue;
        }
        if (interesting_property(property->name)) {
            printf("  %s=", property->name);
            if ((property->flags & DRM_MODE_PROP_SIGNED_RANGE) == DRM_MODE_PROP_SIGNED_RANGE)
                printf("%" PRId64, (int64_t)value);
            else
                printf("%" PRIu64, value);
            if (!strncmp(property->name, "SRC_", 4))
                printf(" (16.16=%.4f)", (double)value / 65536.0);
            if (!strcasecmp(property->name, "rotation"))
                printf(" (mask=0x%" PRIx64 ")", value);
            if (property->flags & DRM_MODE_PROP_ENUM) {
                for (int j = 0; j < property->count_enums; ++j)
                    if (property->enums[j].value == value)
                        printf(" (%s)", property->enums[j].name);
            }
            putchar('\n');
        }
        drmModeFreeProperty(property);
    }
    drmModeFreeObjectProperties(properties);
}

static bool query_fb2(int fd, uint32_t id, struct drm_mode_fb_cmd2 *fb2)
{
    memset(fb2, 0, sizeof(*fb2));
    fb2->fb_id = id;
#ifdef DRM_IOCTL_MODE_GETFB2
    if (drmIoctl(fd, DRM_IOCTL_MODE_GETFB2, fb2) != 0) {
        int error = errno;
        printf("  fb %u GETFB2 unavailable: %s (errno=%d); ", id, strerror(error), error);
        if (error == ENOTTY)
            puts("ABI not implemented for this device; legacy GETFB fallback.");
        else
            puts("ABI support not established (old/vendor ABI or object/access error); legacy GETFB fallback.");
        return false;
    }
    char fourcc[5];
    for (unsigned i = 0; i < 4; ++i) {
        unsigned c = (fb2->pixel_format >> (i * 8)) & 255;
        fourcc[i] = c >= 32 && c <= 126 ? (char)c : '?';
    }
    fourcc[4] = 0;
    printf("  fb %u GETFB2 supported: %ux%u fourcc=%s (0x%08x) flags=0x%x\n",
           id, fb2->width, fb2->height, fourcc, fb2->pixel_format, fb2->flags);
    for (unsigned i = 0; i < 4; ++i)
        if (fb2->pitches[i] || fb2->handles[i] || fb2->offsets[i] || fb2->modifier[i])
            printf("    buffer plane %u: handle=%u pitch=%u offset=%u modifier=0x%016" PRIx64 "\n",
                   i, fb2->handles[i], fb2->pitches[i], fb2->offsets[i], (uint64_t)fb2->modifier[i]);
    return true;
#else
    puts("  GETFB2 unavailable in build headers; ABI was not probed, using legacy GETFB.");
    return false;
#endif
}

static bool print_fb(int fd, uint32_t id)
{
    struct drm_mode_fb_cmd2 fb2;
    (void)query_fb2(fd, id, &fb2);
    drmModeFB *fb = drmModeGetFB(fd, id);
    if (!fb) {
        printf("  fb %u GETFB unavailable: %s\n", id, strerror(errno));
        return false;
    }
    printf("  fb %u: %ux%u pitch=%u bpp=%u depth=%u handle=%u\n",
           id, fb->width, fb->height, fb->pitch, fb->bpp, fb->depth, fb->handle);
    drmModeFreeFB(fb);
    return true;
}

static void print_plane(int fd, const drmModePlane *plane)
{
    printf("plane %u: crtc=%u fb=%u possible_crtcs=0x%x %s\n",
           plane->plane_id, plane->crtc_id, plane->fb_id, plane->possible_crtcs,
           plane->crtc_id && plane->fb_id ? "ACTIVE" : "inactive");
    print_properties(fd, plane->plane_id, DRM_MODE_OBJECT_PLANE);
    if (plane->fb_id)
        print_fb(fd, plane->fb_id);
}

static void pixel_add(struct pixel_stats *s, unsigned r, unsigned g,
                      unsigned b, unsigned byte)
{
    ++s->pixels;
    s->nonblack += (r | g | b) != 0;
    s->byte_zero += byte == 0;
    s->byte_ff += byte == 255;
    s->byte_sum += byte;
    if (byte < s->byte_min) s->byte_min = byte;
    if (byte > s->byte_max) s->byte_max = byte;
}

static void print_stats(const char *label, const struct pixel_stats *s)
{
    if (!s->pixels) return;
    printf("%s: pixels=%" PRIu64 " rgb_nonblack=%.2f%%"
           " high_byte[alpha/X]: min=%u max=%u mean=%.2f zero=%.2f%% ff=%.2f%%\n",
           label, s->pixels, 100.0 * (double)s->nonblack / (double)s->pixels,
           s->byte_min, s->byte_max, (double)s->byte_sum / (double)s->pixels,
           100.0 * (double)s->byte_zero / (double)s->pixels,
           100.0 * (double)s->byte_ff / (double)s->pixels);
}

static void mapping_sigbus(int signal_number)
{
    static const char message[] = "dump failed: SIGBUS reading framebuffer mapping; output is incomplete.\n";
    ssize_t written;
    (void)signal_number;
    written = write(STDERR_FILENO, message, sizeof(message) - 1);
    (void)written;
    _exit(3);
}

static int dump_fb(int fd, uint32_t id, const drmModePlane *plane, const char *path)
{
    struct drm_mode_fb_cmd2 fb2;
    bool has_fb2 = query_fb2(fd, id, &fb2);
    bool swap_red_blue = false;
    uint32_t pixel_offset = 0;
    drmModeFB *fb = drmModeGetFB(fd, id);
    struct drm_mode_map_dumb map = {0};
    struct pixel_stats bands[4] = {{.byte_min=255}, {.byte_min=255},
                                  {.byte_min=255}, {.byte_min=255}};
    struct pixel_stats top = {.byte_min=255};
    struct sigaction action = {.sa_handler=mapping_sigbus}, old_action;
    const volatile unsigned char *pixels = MAP_FAILED;
    unsigned char *row = NULL;
    FILE *out = NULL;
    size_t length = 0;
    bool created = false, handler_installed = false;
    int result = 1;
    if (!fb) {
        fprintf(stderr, "dump: GETFB %u: %s\n", id, strerror(errno));
        return 1;
    }
    if (!fb->handle) {
        fprintf(stderr, "dump: GETFB returned no GEM handle; root/CAP_SYS_ADMIN is"
                " required when another process owns DRM master.\n");
        goto done;
    }
    if (fb->bpp != 32 || (fb->depth != 24 && fb->depth != 32)) {
        fprintf(stderr, "dump: unsupported bpp=%u/depth=%u; only 32/24 and 32/32 supported.\n",
                fb->bpp, fb->depth);
        goto done;
    }
    if (has_fb2) {
        if (fb2.width != fb->width || fb2.height != fb->height || fb2.pitches[0] != fb->pitch) {
            fprintf(stderr, "dump: GETFB and GETFB2 disagree on dimensions/pitch; refusing.\n");
            goto done;
        }
        for (unsigned i = 1; i < 4; ++i)
            if (fb2.handles[i] || fb2.pitches[i] || fb2.offsets[i] || fb2.modifier[i]) {
                fprintf(stderr, "dump: multi-plane framebuffer unsupported.\n");
                goto done;
            }
        if (fb2.modifier[0] != DRM_FORMAT_MOD_LINEAR) {
            fprintf(stderr, "dump: non-linear modifier 0x%016" PRIx64 " unsupported; no linear decode attempted.\n",
                    (uint64_t)fb2.modifier[0]);
            goto done;
        }
        switch (fb2.pixel_format) {
        case DRM_FORMAT_XRGB8888: case DRM_FORMAT_ARGB8888: break;
        case DRM_FORMAT_XBGR8888: case DRM_FORMAT_ABGR8888: swap_red_blue = true; break;
        default:
            fprintf(stderr, "dump: unsupported GETFB2 fourcc 0x%08x.\n", fb2.pixel_format);
            goto done;
        }
        pixel_offset = fb2.offsets[0];
    }
    if (!fb->width || !fb->height || fb->width > 65536 || fb->height > 65536 ||
        (uint64_t)fb->width * 4 > fb->pitch ||
        (uint64_t)pixel_offset + (uint64_t)fb->pitch * fb->height > SIZE_MAX ||
        (uint64_t)pixel_offset + (uint64_t)fb->pitch * fb->height > 512ULL * 1024 * 1024) {
        fprintf(stderr, "dump: invalid or excessive framebuffer dimensions/pitch.\n");
        goto done;
    }
    length = pixel_offset + (size_t)fb->pitch * fb->height;
    map.handle = fb->handle;
    if (drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map) < 0) {
        fprintf(stderr, "dump: MODE_MAP_DUMB unsupported/denied for handle %u: %s\n",
                map.handle, strerror(errno));
        goto done;
    }
    if (map.offset > INT64_MAX) {
        fprintf(stderr, "dump: mmap offset does not fit off_t.\n");
        goto done;
    }
    pixels = mmap(NULL, length, PROT_READ, MAP_SHARED, fd, (off_t)map.offset);
    if (pixels == MAP_FAILED) {
        fprintf(stderr, "dump: read-only mmap %zu bytes at 0x%" PRIx64 ": %s\n",
                length, (uint64_t)map.offset, strerror(errno));
        goto done;
    }
    row = malloc((size_t)fb->width * 3);
    if (!row) {
        fprintf(stderr, "dump: allocating row: %s\n", strerror(errno));
        goto done;
    }
    out = fopen(path, "wx");
    if (!out) {
        fprintf(stderr, "dump: creating %s (existing files refused): %s\n", path, strerror(errno));
        goto done;
    }
    created = true;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGBUS, &action, &old_action) < 0) {
        fprintf(stderr, "dump: installing SIGBUS handler: %s\n", strerror(errno));
        goto done;
    }
    handler_installed = true;
    if (fprintf(out, "P6\n# %s\n%u %u\n255\n",
                has_fb2 ? "GETFB2 linear 8888 RGB decode; framebuffer offset applied."
                        : "Assumed linear little-endian XRGB/ARGB8888; GETFB has no fourcc/modifier/offset.",
                fb->width, fb->height) < 0)
        goto write_error;
    printf("dump plane=%u crtc=%u fb=%u %ux%u pitch=%u bpp=%u depth=%u offset=%u\n",
           plane ? plane->plane_id : 0, plane ? plane->crtc_id : 0, id,
           fb->width, fb->height, fb->pitch, fb->bpp, fb->depth, pixel_offset);
    if (has_fb2)
        puts("decode: GETFB2 reports supported little-endian 8888 format and linear modifier; offset applied.");
    else {
        puts("decode ASSUMPTION: linear little-endian XRGB8888/ARGB8888, offset=0."
             " Legacy GETFB does not expose fourcc/modifiers/offsets;");
        puts("tiling/UBWC and other channel order cannot be ruled out.");
    }
    puts("No cache writes or synchronization performed: stale CPU cache, concurrent rendering,"
         " or vendor layout quirks cannot be ruled out.");
    for (uint32_t y = 0; y < fb->height; ++y) {
        const volatile unsigned char *source = pixels + pixel_offset + (size_t)y * fb->pitch;
        unsigned band = (unsigned)((uint64_t)y * 4 / fb->height);
        for (uint32_t x = 0; x < fb->width; ++x) {
            unsigned b = source[(size_t)x * 4];
            unsigned g = source[(size_t)x * 4 + 1];
            unsigned r = source[(size_t)x * 4 + 2];
            unsigned high = source[(size_t)x * 4 + 3];
            if (swap_red_blue) { unsigned tmp = r; r = b; b = tmp; }
            row[(size_t)x * 3] = (unsigned char)r;
            row[(size_t)x * 3 + 1] = (unsigned char)g;
            row[(size_t)x * 3 + 2] = (unsigned char)b;
            pixel_add(&bands[band], r, g, b, high);
            if (y < 64) pixel_add(&top, r, g, b, high);
        }
        if (fwrite(row, 3, fb->width, out) != fb->width)
            goto write_error;
    }
    if (fclose(out) != 0) {
        out = NULL;
        goto write_error;
    }
    out = NULL;
    print_stats("top 64 rows", &top);
    for (unsigned i = 0; i < 4; ++i) {
        char label[64];
        snprintf(label, sizeof(label), "vertical quarter %u (top=1)", i + 1);
        print_stats(label, &bands[i]);
    }
    printf("PPM written: %s; alpha/X omitted from RGB file.\n", path);
    if (plane) {
        drmModePlane *after = drmModeGetPlane(fd, plane->plane_id);
        if (!after || after->fb_id != plane->fb_id || after->crtc_id != plane->crtc_id)
            puts("NOTE: active plane changed during dump; image is the retained earlier FB,"
                 " not a synchronized scanout capture.");
        if (after) drmModeFreePlane(after);
    } else puts("NOTE: --fb captures the explicitly named buffer; current scanout association is not verified.");
    result = 0;
    goto done;
write_error:
    fprintf(stderr, "dump: writing %s: %s\n", path, strerror(errno));
done:
    if (handler_installed) sigaction(SIGBUS, &old_action, NULL);
    if (out) fclose(out);
    if (created && result != 0) unlink(path);
    free(row);
    if (pixels != MAP_FAILED) munmap((void *)pixels, length);
    drmModeFreeFB(fb);
    return result;
}

int main(int argc, char **argv)
{
    const char *device = NULL, *dump = NULL;
    uint32_t wanted_plane = 0, wanted_fb = 0;
    drmModeRes *resources = NULL;
    drmModePlaneRes *planes = NULL;
    drmModePlane *selected = NULL;
    unsigned active_count = 0;
    bool complete_plane_view, legacy_client = false;
    int fd, result = 0;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--help")) { usage(stdout, argv[0]); return 0; }
        if (!strcmp(argv[i], "--legacy-client")) {
            legacy_client = true;
        } else if (!strcmp(argv[i], "--plane") && i + 1 < argc) {
            if (!parse_id(argv[++i], &wanted_plane)) {
                fprintf(stderr, "Invalid plane ID: %s\n", argv[i]); return 2;
            }
        } else if (!strcmp(argv[i], "--fb") && i + 1 < argc) {
            if (!parse_id(argv[++i], &wanted_fb)) {
                fprintf(stderr, "Invalid framebuffer ID: %s\n", argv[i]); return 2;
            }
        } else if (!strcmp(argv[i], "--dump") && i + 1 < argc) {
            dump = argv[++i];
        } else if (!device && argv[i][0] != '-') {
            device = argv[i];
        } else { usage(stderr, argv[0]); return 2; }
    }
    if (!device) { usage(stderr, argv[0]); return 2; }
    if (wanted_plane && wanted_fb) {
        fprintf(stderr, "--plane and --fb are mutually exclusive; select one explicit object.\n");
        return 2;
    }
    fd = open(device, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        fprintf(stderr, "open %s read-only: %s\n", device, strerror(errno)); return 1;
    }
    drmVersion *version = drmGetVersion(fd);
    if (version) {
        printf("device=%s driver=%.*s version=%d.%d.%d\n", device,
               version->name_len, version->name ? version->name : "",
               version->version_major, version->version_minor, version->version_patchlevel);
        drmFreeVersion(version);
    } else {
        fprintf(stderr, "DRM version unavailable: %s\n", strerror(errno));
        result = 1;
        goto done;
    }
    complete_plane_view = drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) == 0;
    if (!complete_plane_view)
        printf("NOTE: universal plane query view unavailable: %s; primary/cursor planes may be hidden.\n",
               strerror(errno));
    printf("client query view: %s (UNIVERSAL_PLANES=%u)\n",
           legacy_client ? "legacy, ATOMIC not enabled" : "atomic requested", complete_plane_view ? 1u : 0u);
    if (!legacy_client && drmSetClientCap(fd, DRM_CLIENT_CAP_ATOMIC, 1) != 0)
        printf("NOTE: atomic property query view unavailable: %s; SRC/CRTC properties may be hidden.\n",
               strerror(errno));
    puts("NOTE: a new DRM fd may observe zero/missing software state on vendor drivers."
         " Such results do not establish absence of hardware scanout; compare --legacy-client"
         " and other capture methods.");
    if (wanted_fb) {
        printf("explicit framebuffer query: FB=%u; scanout association is not inferred.\n", wanted_fb);
        if (!print_fb(fd, wanted_fb)) result = 1;
        if (dump) result = dump_fb(fd, wanted_fb, NULL, dump);
        goto done;
    }
    resources = drmModeGetResources(fd);
    if (!resources) {
        fprintf(stderr, "GETRESOURCES: %s\n", strerror(errno));
        result = 1;
        goto done;
    }
    printf("resources: connectors=%d encoders=%d crtcs=%d fbs=%d limits=%ux%u..%ux%u\n",
           resources->count_connectors, resources->count_encoders, resources->count_crtcs,
           resources->count_fbs, resources->min_width, resources->min_height,
           resources->max_width, resources->max_height);
    for (int i = 0; i < resources->count_connectors; ++i) {
        /* Current avoids the connector reprobe done by drmModeGetConnector. */
        drmModeConnector *c = drmModeGetConnectorCurrent(fd, resources->connectors[i]);
        if (!c) { printf("connector %u unavailable: %s\n", resources->connectors[i], strerror(errno)); continue; }
        drmModeEncoder *encoder = c->encoder_id ? drmModeGetEncoder(fd, c->encoder_id) : NULL;
        const char *type = drmModeGetConnectorTypeName(c->connector_type);
        printf("connector %u %s-%u: %s encoder=%u crtc=%u modes=%d size_mm=%ux%u\n",
               c->connector_id, type ? type : "unknown", c->connector_type_id,
               connection_name(c->connection), c->encoder_id,
               encoder ? encoder->crtc_id : 0, c->count_modes, c->mmWidth, c->mmHeight);
        print_properties(fd, c->connector_id, DRM_MODE_OBJECT_CONNECTOR);
        if (encoder) drmModeFreeEncoder(encoder);
        drmModeFreeConnector(c);
    }
    for (int i = 0; i < resources->count_encoders; ++i) {
        drmModeEncoder *e = drmModeGetEncoder(fd, resources->encoders[i]);
        if (!e) { printf("encoder %u unavailable: %s\n", resources->encoders[i], strerror(errno)); continue; }
        printf("encoder %u: type=%u crtc=%u possible_crtcs=0x%x\n",
               e->encoder_id, e->encoder_type, e->crtc_id, e->possible_crtcs);
        drmModeFreeEncoder(e);
    }
    for (int i = 0; i < resources->count_crtcs; ++i) {
        drmModeCrtc *c = drmModeGetCrtc(fd, resources->crtcs[i]);
        if (!c) { printf("crtc %u unavailable: %s\n", resources->crtcs[i], strerror(errno)); continue; }
        printf("crtc %u: mode_valid=%d legacy_fb=%u legacy_xy=%u,%u",
               c->crtc_id, c->mode_valid, c->buffer_id, c->x, c->y);
        if (c->mode_valid)
            printf(" mode=%s %ux%u refresh=%u clock_khz=%u flags=0x%x",
                   c->mode.name, c->mode.hdisplay, c->mode.vdisplay,
                   c->mode.vrefresh, c->mode.clock, c->mode.flags);
        putchar('\n');
        print_properties(fd, c->crtc_id, DRM_MODE_OBJECT_CRTC);
        if (c->buffer_id) print_fb(fd, c->buffer_id);
        drmModeFreeCrtc(c);
    }
    planes = drmModeGetPlaneResources(fd);
    if (!planes) {
        fprintf(stderr, "GETPLANERESOURCES: %s\n", strerror(errno));
        result = 1;
        goto done;
    }
    printf("planes: total_visible=%u\n", planes->count_planes);
    for (uint32_t i = 0; i < planes->count_planes; ++i) {
        drmModePlane *p = drmModeGetPlane(fd, planes->planes[i]);
        if (!p) { printf("plane %u unavailable: %s\n", planes->planes[i], strerror(errno)); continue; }
        print_plane(fd, p);
        if (p->crtc_id && p->fb_id) {
            ++active_count;
            if (!wanted_plane && !selected) { selected = p; continue; }
        }
        if (wanted_plane == p->plane_id) { selected = p; continue; }
        drmModeFreePlane(p);
    }
    if (wanted_plane && !selected) {
        selected = drmModeGetPlane(fd, wanted_plane);
        if (selected) print_plane(fd, selected);
        else fprintf(stderr, "requested plane %u unavailable: %s\n", wanted_plane, strerror(errno));
    }
    printf("active planes observed=%u; DRM queries are sequential, not an atomic snapshot.\n", active_count);
    if (!active_count)
        puts("NOTE: no active plane observed on this fd; hardware output may still be active."
             " Try --legacy-client or --fb ID from an independently observed FB.");
    if (wanted_plane && !selected) result = 1;
    if (dump) {
        if (!wanted_plane && (!complete_plane_view || active_count != 1)) {
            fprintf(stderr, "dump refused: %u observed active planes%s; select explicitly with --plane ID.\n",
                    active_count, complete_plane_view ? "" : ", incomplete plane view");
            result = 1;
        } else if (!selected || !selected->crtc_id || !selected->fb_id) {
            fprintf(stderr, "dump refused: selected plane is missing or inactive.\n");
            result = 1;
        } else result = dump_fb(fd, selected->fb_id, selected, dump);
    }
done:
    if (selected) drmModeFreePlane(selected);
    if (planes) drmModeFreePlaneResources(planes);
    if (resources) drmModeFreeResources(resources);
    close(fd);
    return result;
}
