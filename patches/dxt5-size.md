# dxt5-size: DXT5 texture allocation

A TES3X fix. The retail game ships no DXT5 textures, so the defect never shows in it; mods do
ship them, and every one corrupts video memory when it loads.

## The defect

Textures are created at `0x00023AB0`, which asks `0x00023CF0` for the size of the mip chain.
That function knows two compressed formats: DXT1 (D3D format `0xC`, 8 bytes per 4x4 block) and
DXT3 (`0xE`, 16 bytes per block). The engine maps a DXT5 file to D3D format `0xF`
(`0x00015740`), which falls through to the uncompressed path. There the bits-per-pixel table
(`0x0001BB30`) returns -1, so the size comes out as roughly minus the texture's pixel count.

The contiguous allocator at `0x00015100` compares sizes signed. A negative request fits the first
free block whatever its size, and splitting that block writes a free-block header before it,
inside live memory. With no free block, the request is taken from the top of the heap and moves
the used mark backwards, so later allocations overlap the texture. Vertex buffers live in the same
arena, with their D3D headers in front of their data, so the damage surfaces far from its cause:
float vertex data in a vertex buffer's `Data` field (`LandVertex::GetPosition`, `0x00114CDA`;
the land vertex writer, `0x00114EA5`; the particle quad builder, `0x00018949`), a corrupt free
list in the allocator itself (`0x00015153`), or xemu's `attr->offset < dma_len` assertion when
the GPU reads a vertex buffer outside its memory.

## What the patch changes

The call to the size function at `0x00023AC8` goes through a hook in `hooks/tes3xdxt5.c`, which
passes format `0xF` on as `0xE` and every other format unchanged. DXT5 has DXT3's block size, so
the allocation is exact. The texture keeps format `0xF`; only its size is computed as DXT3.

## What remains

A texture set that fitted only because its DXT5 textures cost nothing now costs its real size. The
engine's answer to exhausted video memory is its own out-of-memory handler (`0x00092BA0`), which
purges what it can and then relaunches the title with the "disc may be dirty or damaged" error.
