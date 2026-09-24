/* Size DXT5 textures correctly. The engine's texture-size function knows only DXT1 (0xC) and
 * DXT3 (0xE); DXT5 (0xF) falls through to a bits-per-pixel table that returns -1, so the
 * contiguous allocator is asked for a negative size and hands out overlapping memory.
 * DXT5 has DXT3's 16-byte blocks, so size it as DXT3.
 */

#ifndef TES3X_TEXTURE_SIZE
#error "define TES3X_TEXTURE_SIZE to the VA of the texture-size function"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

/* Called in place of the size function: format in eax, width ecx, height edx, levels on the
 * stack. Tail-jumps, so the caller's argument and return address are left as they were. */
__attribute__((naked)) void tes3x_dxt5_size_hook(void)
{
    __asm__ volatile(
        "cmpl $0xf, %eax\n\t"
        "jne 1f\n\t"
        "movl $0xe, %eax\n\t"
        "1:\n\t"
        "pushl $" TES3X_STR(TES3X_TEXTURE_SIZE) "\n\t"
        "ret\n\t");
}
