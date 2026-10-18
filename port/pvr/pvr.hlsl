// The PowerVR's pixel pipeline for one batch of triangles, compiled by fxc at build time (CMakeLists.txt).
//
// Vertices arrive in 640x480 screen space with z = 1/w. The vertex shader rebuilds clip space with W = 1/z, so the
// rasterizer interpolates UVs and colours perspective-correct as the PowerVR does, and writes depth = z * scale
// (scale: 1 / the frame's largest 1/w), which keeps "greater" meaning nearer. The pixel shader applies the TSP word
// of its batch: texture/shading instruction, alpha use, offset colour, fog (table, per vertex, table mode 2) and
// the punch-through alpha test.

cbuffer Frame : register(b0)
{
    float depth_scale;
    float fog_density;   // FOG_DENSITY as a float: mantissa / 128 * 2^exponent
    float punch_ref;     // PT_ALPHA_REF / 255
    float shadow_alpha;  // 1 - FPU_SHAD_SCALE's intensity / 256 (cheap shadow)
    float4 fog_color;    // FOG_COL_RAM
    uint4 fog_table[32]; // FOG_TABLE: 128 entries, (this density << 8) | the next one's
};

struct Batch { uint isp, tsp, tcw, flags; };
StructuredBuffer<Batch> batches : register(t1);
Texture2D tex : register(t0);
SamplerState smp : register(s0);

#define BF_TEXTURE 1u
#define BF_OFFSET 2u
#define BF_PUNCH 4u
#define TSP_USE_ALPHA (1u << 20)
#define TSP_IGNORE_TEX_ALPHA (1u << 19)

struct VSIn
{
    float3 pos : POSITION;
    float2 uv : TEXCOORD0;
    float4 base : COLOR0;
    float4 off : COLOR1;
    uint batch : BATCH;
};

struct PSIn
{
    float4 pos : SV_Position;
    float2 uv : TEXCOORD0;
    float4 base : COLOR0;
    float4 off : COLOR1;
    noperspective float invw : TEXCOORD1;
    nointerpolation uint batch : BATCH;
};

PSIn vs_main(VSIn i)
{
    PSIn o;
    float w = 1.0 / i.pos.z;
    o.pos = float4((i.pos.x / 320.0 - 1.0) * w, (1.0 - i.pos.y / 240.0) * w, i.pos.z * depth_scale * w, w);
    o.uv = i.uv;
    o.base = i.base;
    o.off = i.off;
    o.invw = i.pos.z;
    o.batch = i.batch;
    return o;
}

// The table's entry for 1/w * density: its float exponent picks 16 entries, the top mantissa bits one of them, and
// the rest of the mantissa blends towards the next.
float fog_lookup(float invw)
{
    float z = clamp(invw * fog_density, 1.0, 255.9999);
    float e = floor(log2(z));
    float m = z * 16.0 / exp2(e) - 16.0;
    uint idx = (uint)(e * 16.0 + floor(m));
    uint entry = fog_table[idx >> 2][idx & 3u];
    return lerp((float)(entry >> 8) / 255.0, (float)(entry & 255u) / 255.0, frac(m));
}

// The batch's colour for one pixel, before the punch-through test and the blend.
float4 shade(PSIn i, Batch b)
{
    float4 col = i.base;
    float4 c;
    uint fog = (b.tsp >> 22) & 3u;
    if (!(b.tsp & TSP_USE_ALPHA))
        col.a = 1.0;
    c = col;
    if (b.flags & BF_TEXTURE) {
        float4 t = tex.Sample(smp, i.uv);
        uint instr = (b.tsp >> 6) & 3u;
        if (b.tsp & TSP_IGNORE_TEX_ALPHA)
            t.a = 1.0;
        if (instr == 0u)
            c = t;
        else if (instr == 1u)
            c = float4(t.rgb * col.rgb, t.a);
        else if (instr == 2u)
            c = float4(lerp(col.rgb, t.rgb, t.a), col.a);
        else
            c = t * col;
        if (b.flags & BF_OFFSET)
            c.rgb += i.off.rgb;
    }
    if (fog == 0u)
        c.rgb = lerp(c.rgb, fog_color.rgb, fog_lookup(i.invw));
    else if (fog == 1u)
        c.rgb = lerp(c.rgb, fog_color.rgb, i.off.a);
    else if (fog == 3u)
        c = float4(fog_color.rgb, fog_lookup(i.invw));
    return saturate(c);
}

float4 ps_main(PSIn i) : SV_Target
{
    Batch b = batches[i.batch];
    float4 c = shade(i, b);
    if ((b.flags & BF_PUNCH) && c.a < punch_ref)
        discard;
    return c;
}

// Cheap shadow (pvr_modvol.c): over the shadow pixels inside a modifier volume, blended as source alpha / inverse
// source alpha, black at 1 - intensity leaves the colour scaled by the intensity.
float4 ps_shadow() : SV_Target
{
    return float4(0.0, 0.0, 0.0, shadow_alpha);
}
