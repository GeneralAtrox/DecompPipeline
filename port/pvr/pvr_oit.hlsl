// The PowerVR's autosorted translucency, per pixel: the gather pass appends every translucent fragment (its shaded
// colour, 1/w and batch) to the pixel's list; the resolve pass sorts each list far to near and blends the fragments
// over the opaque picture with each one's own TSP blend factors, as the PowerVR's sorter feeds them to the blender.
// Fragments with the same 1/w keep submission order: the batch index, then the triangle within the batch's draw (so
// the result does not depend on the order the GPU appended them). Adapted from Lodoss's render_d3d_transparency
// (MIT, the user's own).
// Translucent modifier volumes act per fragment, as the PowerVR tests each translucent layer against them: a
// fragment of a shadow-bit polygon (N_SHADOW) has its parity flipped by each volume face in front of it (ps_volume);
// at a volume's last triangles the parity joins the inside bit (ps_merge, inclusion) or is dropped (exclusion, a
// to-do: its faces drawn again undo the parity); the resolve scales an inside fragment's colour by the cheap shadow intensity (pvr_modvol.c).

#include "pvr.hlsl"

#define OIT_END 0xffffffffu
#define OIT_MAX 48u // fragments per pixel the resolve sorts; beyond that the first drawn are dropped
#define N_SHADOW 0x80000000u // Node.prim's top bits: the polygon has the PCW shadow bit (BF_SHADOW)
#define N_PARITY 0x40000000u // the faces of the volume being drawn in front of the fragment, odd
#define N_INSIDE 0x20000000u // an inclusion volume encloses the fragment
#define N_PRIM 0x1fffffffu   // the triangle within the batch's draw
#define BF_SHADOW 8u

struct Node { uint color, depth, batch, prim, next; };

RWTexture2D<uint> heads_out : register(u0);
RWStructuredBuffer<Node> nodes_out : register(u1);
Texture2D<uint> heads : register(t2);
StructuredBuffer<Node> nodes : register(t3);
Texture2D<float4> opaque : register(t4);

[earlydepthstencil]
void ps_gather(PSIn i, uint prim : SV_PrimitiveID)
{
    Batch b = batches[i.batch];
    float4 c = shade(i, b);
    uint n = nodes_out.IncrementCounter();
    uint cap, stride;
    Node node;
    nodes_out.GetDimensions(cap, stride);
    if (n >= cap)
        return;
    node.color = (uint)(c.r * 255.0 + 0.5) | (uint)(c.g * 255.0 + 0.5) << 8 | (uint)(c.b * 255.0 + 0.5) << 16 |
                 (uint)(c.a * 255.0 + 0.5) << 24;
    node.depth = asuint(i.invw);
    node.batch = i.batch;
    node.prim = (prim & N_PRIM) | ((b.flags & BF_SHADOW) ? N_SHADOW : 0u);
    InterlockedExchange(heads_out[uint2(i.pos.xy)], n, node.next);
    nodes_out[n] = node;
}

// A translucent modifier volume's triangle: flips the parity of the shadow fragments behind it.
void ps_volume(PSIn i)
{
    uint count = 0u;
    for (uint n = heads_out[uint2(i.pos.xy)]; n != OIT_END && count < OIT_MAX; n = nodes_out[n].next, count++)
        if ((nodes_out[n].prim & N_SHADOW) && i.invw > asfloat(nodes_out[n].depth))
            InterlockedXor(nodes_out[n].prim, N_PARITY);
}

// An inclusion volume's triangles again at its last one: the enclosed fragments (parity set) become inside and the
// parity is cleared for the next volume. Every write in the draw is the same, so overlaps are harmless. (The volume's
// own triangles carry instruction 0, so the mode is the caller's: pvr_modvol.c.)
void ps_merge(PSIn i)
{
    uint count = 0u;
    for (uint n = heads_out[uint2(i.pos.xy)]; n != OIT_END && count < OIT_MAX; n = nodes_out[n].next, count++) {
        uint p = nodes_out[n].prim;
        if (p & N_PARITY)
            nodes_out[n].prim = (p & ~N_PARITY) | N_INSIDE;
    }
}

float4 vs_full(uint id : SV_VertexID) : SV_Position
{
    float2 p = float2((id << 1) & 2u, id & 2u);
    return float4(p * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
}

// A PowerVR blend factor (TSP SRC_INSTR / DST_INSTR): 2 and 3 are the other side's colour.
float4 factor(uint f, float4 src, float4 dst, bool is_src)
{
    float4 other = is_src ? dst : src;
    switch (f) {
    case 0u: return 0.0;
    case 1u: return 1.0;
    case 2u: return other;
    case 3u: return 1.0 - other;
    case 4u: return src.a;
    case 5u: return 1.0 - src.a;
    case 6u: return dst.a;
    default: return 1.0 - dst.a;
    }
}

float4 unpack(uint c)
{
    return float4(c & 255u, (c >> 8) & 255u, (c >> 16) & 255u, c >> 24) / 255.0;
}

float4 ps_resolve(float4 pos : SV_Position) : SV_Target
{
    uint2 px = uint2(pos.xy);
    float4 dst = opaque[px];
    uint list[OIT_MAX];
    uint count = 0u;
    for (uint n = heads[px]; n != OIT_END && count < OIT_MAX; n = nodes[n].next)
        list[count++] = n;
    // Insertion sort: far (small 1/w) first; equal depths in submission order.
    for (uint k = 1u; k < count; k++) {
        uint cur = list[k];
        float d = asfloat(nodes[cur].depth);
        uint bt = nodes[cur].batch, pr = nodes[cur].prim & N_PRIM;
        int j = (int)k - 1;
        for (; j >= 0; j--) {
            Node o = nodes[list[j]];
            float od = asfloat(o.depth);
            uint op = o.prim & N_PRIM;
            if (od < d || (od == d && (o.batch < bt || (o.batch == bt && op < pr))))
                break;
            list[j + 1] = list[j];
        }
        list[j + 1] = cur;
    }
    for (uint f = 0u; f < count; f++) {
        Node node = nodes[list[f]];
        uint tsp = batches[node.batch].tsp;
        float4 src = unpack(node.color);
        if ((node.prim & (N_SHADOW | N_INSIDE)) == (N_SHADOW | N_INSIDE))
            src.rgb *= 1.0 - shadow_alpha; // cheap shadow
        dst = saturate(src * factor(tsp >> 29, src, dst, true) + dst * factor((tsp >> 26) & 7u, src, dst, false));
    }
    return dst;
}
