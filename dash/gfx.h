#ifndef MGR_GFX_H
#define MGR_GFX_H

/* A 640x480 software canvas: alpha-blended fills, gradients and text. Portable C, so the
 * screens also render on the PC (preview/). */

#define GFX_W 640
#define GFX_H 480

typedef unsigned int gfx_color; /* 0xAARRGGBB */

struct glyph {
    unsigned short x, y; /* in the atlas */
    unsigned char w, h;
    signed char ox, oy;  /* from the pen position on the baseline */
    unsigned char adv;
};

struct font {
    const unsigned char *alpha; /* coverage atlas */
    int stride;
    int first, count;
    int ascent, line;
    const struct glyph *g;
};

/* An ARGB image, for skins loaded at runtime. */
struct image {
    const unsigned int *px;
    int w, h;
};

extern unsigned int gfx_pixels[GFX_W * GFX_H];
extern const struct font font_body, font_bold, font_small, font_title, font_logo;

void gfx_clip(int x, int y, int w, int h);
void gfx_noclip(void);
void gfx_fill(int x, int y, int w, int h, gfx_color c);
void gfx_gradient(int x, int y, int w, int h, gfx_color top, gfx_color bottom);
void gfx_outline(int x, int y, int w, int h, gfx_color c);
void gfx_disc(int cx, int cy, int r, gfx_color c);
void gfx_blit(const struct image *im, int sx, int sy, int w, int h, int x, int y);
/* Text with its top at y; returns the x after it. */
int gfx_text(const struct font *f, int x, int y, gfx_color c, const char *s);
int gfx_text_width(const struct font *f, const char *s);
/* Text cut to w pixels, ending in "..." when cut. */
int gfx_text_fit(const struct font *f, int x, int y, int w, gfx_color c, const char *s);
/* Text wrapped at w pixels; returns the lines used. A colour of 0 only counts them. */
int gfx_text_wrap(const struct font *f, int x, int y, int w, gfx_color c, const char *s);

#endif
