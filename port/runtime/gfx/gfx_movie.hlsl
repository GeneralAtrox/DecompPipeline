// A movie frame (runtime/gfx/gfx_movie.c) over the picture: its Y, Cb and Cr planes sampled bilinearly (chroma at
// half size, sited between the luma samples as MPEG-1's are) and turned into RGB by ITU-R BT.601's studio-range
// matrix. Drawn with pvr_full_vs's full-screen triangle into a viewport the size of the movie's rectangle.
Texture2D<float> plane_y : register(t0);
Texture2D<float> plane_cb : register(t1);
Texture2D<float> plane_cr : register(t2);
SamplerState linear_clamp : register(s0);

cbuffer Rect : register(b0)
{
    float4 rect; // x, y, width, height of the viewport, in pixels
};

float4 ps_movie(float4 pos : SV_Position) : SV_Target
{
    float2 uv = (pos.xy - rect.xy) / rect.zw;
    float y = plane_y.Sample(linear_clamp, uv) * 255.0 - 16.0;
    float cb = plane_cb.Sample(linear_clamp, uv) * 255.0 - 128.0;
    float cr = plane_cr.Sample(linear_clamp, uv) * 255.0 - 128.0;
    float3 rgb = float3(1.164 * y + 1.596 * cr, 1.164 * y - 0.392 * cb - 0.813 * cr, 1.164 * y + 2.017 * cb);
    return float4(saturate(rgb / 255.0), 1.0);
}
