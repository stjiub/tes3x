#include "gfx.h"

#include <string.h>

unsigned int gfx_pixels[GFX_W * GFX_H];
static int clip_x0, clip_y0, clip_x1 = GFX_W, clip_y1 = GFX_H;

void gfx_clip(int x, int y, int w, int h)
{
    clip_x0 = x < 0 ? 0 : x;
    clip_y0 = y < 0 ? 0 : y;
    clip_x1 = x + w > GFX_W ? GFX_W : x + w;
    clip_y1 = y + h > GFX_H ? GFX_H : y + h;
}

void gfx_noclip(void)
{
    gfx_clip(0, 0, GFX_W, GFX_H);
}

static unsigned int blend(unsigned int d, gfx_color c, unsigned a)
{
    unsigned rb = d & 0xFF00FF, g = d & 0x00FF00;

    rb += (((c & 0xFF00FF) - rb) * a >> 8) & 0xFF00FF;
    g += (((c & 0x00FF00) - g) * a >> 8) & 0x00FF00;
    return 0xFF000000 | rb | g;
}

/* Clips a rectangle in place; zero when nothing is left. */
static int clip(int *x, int *y, int *w, int *h)
{
    int x1 = *x + *w, y1 = *y + *h;

    if (*x < clip_x0)
        *x = clip_x0;
    if (*y < clip_y0)
        *y = clip_y0;
    if (x1 > clip_x1)
        x1 = clip_x1;
    if (y1 > clip_y1)
        y1 = clip_y1;
    *w = x1 - *x;
    *h = y1 - *y;
    return *w > 0 && *h > 0;
}

void gfx_fill(int x, int y, int w, int h, gfx_color c)
{
    unsigned a = c >> 24, *p;
    int i, j;

    if (!a || !clip(&x, &y, &w, &h))
        return;
    a += a >> 7; /* 255 -> 256 */
    for (j = 0; j < h; j++) {
        p = &gfx_pixels[(y + j) * GFX_W + x];
        if (a == 256)
            for (i = 0; i < w; i++)
                p[i] = c | 0xFF000000;
        else
            for (i = 0; i < w; i++)
                p[i] = blend(p[i], c, a);
    }
}

static gfx_color mix(gfx_color a, gfx_color b, int t, int n)
{
    gfx_color out = 0;
    int shift, ca, cb;

    for (shift = 0; shift < 32; shift += 8) {
        ca = a >> shift & 0xFF;
        cb = b >> shift & 0xFF;
        out |= (gfx_color)(ca + (cb - ca) * t / n) << shift;
    }
    return out;
}

void gfx_gradient(int x, int y, int w, int h, gfx_color top, gfx_color bottom)
{
    int j;

    for (j = 0; j < h; j++)
        gfx_fill(x, y + j, w, 1, mix(top, bottom, j, h > 1 ? h - 1 : 1));
}

void gfx_outline(int x, int y, int w, int h, gfx_color c)
{
    gfx_fill(x, y, w, 1, c);
    gfx_fill(x, y + h - 1, w, 1, c);
    gfx_fill(x, y + 1, 1, h - 2, c);
    gfx_fill(x + w - 1, y + 1, 1, h - 2, c);
}

void gfx_disc(int cx, int cy, int r, gfx_color c)
{
    int x, y, d, limit = r * r * 64, cover, sx, sy, a = c >> 24;

    for (y = -r; y < r; y++)
        for (x = -r; x < r; x++) {
            /* 4x4 samples a pixel, in eighths of a pixel from the centre */
            cover = 0;
            for (sy = 0; sy < 4; sy++)
                for (sx = 0; sx < 4; sx++) {
                    d = (8 * x + 2 * sx + 1) * (8 * x + 2 * sx + 1)
                        + (8 * y + 2 * sy + 1) * (8 * y + 2 * sy + 1);
                    cover += d <= limit;
                }
            if (cover)
                gfx_fill(cx + x, cy + y, 1, 1, (c & 0xFFFFFF) | (unsigned)(a * cover / 16) << 24);
        }
}

void gfx_blit(const struct image *im, int sx, int sy, int w, int h, int x, int y)
{
    int x0 = x, y0 = y, i, j;
    unsigned s, a, *p;

    if (!clip(&x, &y, &w, &h))
        return;
    sx += x - x0;
    sy += y - y0;
    for (j = 0; j < h; j++) {
        p = &gfx_pixels[(y + j) * GFX_W + x];
        for (i = 0; i < w; i++) {
            s = im->px[(sy + j) * im->w + sx + i];
            a = s >> 24;
            if (a == 255)
                p[i] = s;
            else if (a)
                p[i] = blend(p[i], s, a + (a >> 7));
        }
    }
}

static const struct glyph *glyph(const struct font *f, unsigned char ch)
{
    if (ch < f->first || ch >= f->first + f->count)
        ch = '?';
    return &f->g[ch - f->first];
}

static void draw_glyph(const struct font *f, const struct glyph *g, int x, int y, gfx_color c)
{
    int i, j, gx = x + g->ox, gy = y + f->ascent + g->oy;
    unsigned a, alpha = c >> 24, *p;
    const unsigned char *src;

    for (j = 0; j < g->h; j++) {
        if (gy + j < clip_y0 || gy + j >= clip_y1)
            continue;
        p = &gfx_pixels[(gy + j) * GFX_W];
        src = f->alpha + (g->y + j) * f->stride + g->x;
        for (i = 0; i < g->w; i++) {
            if (gx + i < clip_x0 || gx + i >= clip_x1 || !src[i])
                continue;
            a = src[i] * alpha / 255;
            p[gx + i] = blend(p[gx + i], c, a + (a >> 7));
        }
    }
}

int gfx_text(const struct font *f, int x, int y, gfx_color c, const char *s)
{
    const struct glyph *g;

    for (; *s; s++) {
        g = glyph(f, (unsigned char)*s);
        draw_glyph(f, g, x, y, c);
        x += g->adv;
    }
    return x;
}

int gfx_text_width(const struct font *f, const char *s)
{
    int w = 0;

    for (; *s; s++)
        w += glyph(f, (unsigned char)*s)->adv;
    return w;
}

int gfx_text_fit(const struct font *f, int x, int y, int w, gfx_color c, const char *s)
{
    int dots = 3 * glyph(f, '.')->adv, used = 0;
    const char *p;

    if (gfx_text_width(f, s) <= w)
        return gfx_text(f, x, y, c, s);
    for (p = s; *p && used + glyph(f, (unsigned char)*p)->adv + dots <= w; p++)
        used += glyph(f, (unsigned char)*p)->adv;
    for (; s < p; s++) {
        draw_glyph(f, glyph(f, (unsigned char)*s), x, y, c);
        x += glyph(f, (unsigned char)*s)->adv;
    }
    return gfx_text(f, x, y, c, "...");
}

int gfx_text_wrap(const struct font *f, int x, int y, int w, gfx_color c, const char *s)
{
    char line[256];
    const char *start = s, *end, *word;
    int lines = 0, n;

    while (*start) {
        /* longest run of whole words that fits */
        end = NULL;
        for (word = start; *word;) {
            while (*word && *word != ' ')
                word++;
            n = (int)(word - start);
            if (n >= (int)sizeof(line))
                break;
            memcpy(line, start, n);
            line[n] = 0;
            if (end && gfx_text_width(f, line) > w)
                break;
            end = word;
            while (*word == ' ')
                word++;
        }
        if (!end)
            end = word;
        n = (int)(end - start) < (int)sizeof(line) ? (int)(end - start) : (int)sizeof(line) - 1;
        memcpy(line, start, n);
        line[n] = 0;
        if (c)
            gfx_text_fit(f, x, y + lines * f->line, w, c, line);
        lines++;
        for (start = end; *start == ' '; start++)
            ;
    }
    return lines;
}
