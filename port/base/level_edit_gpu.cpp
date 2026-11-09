// The level editor's meshes in the viewport (level_edit.cpp; level_edit_priv.hpp shares the state): its own small
// Direct3D 11 pipeline on the base's device (shaders, a texture atlas, a vertex buffer per mesh, reversed depth), and
// the editor's picture written as a BMP (--level-edit-shot).
// Nearest existing: level_edit.cpp - split from it at 400 lines.
#include <SDL3/SDL.h> // the editor runs on SDL3 (also through level_edit_priv.hpp): built by the game build
#include "level_edit_priv.hpp"

namespace level_ed {

// ---- Direct3D: meshes in the viewport ----
struct GpuVert {
    float x, y, z, nx, ny, nz, u, v;
    u32 rgba;
};
struct Gpu {
    ID3D11VertexShader *vs;
    ID3D11PixelShader *ps;
    ID3D11InputLayout *layout;
    ID3D11Buffer *cb;
    ID3D11SamplerState *sampler;
    ID3D11RasterizerState *raster;
    ID3D11DepthStencilState *depth_on;
    ID3D11BlendState *blend;
    ID3D11Texture2D *atlas;
    ID3D11ShaderResourceView *atlas_srv;
    ID3D11Texture2D *depth;
    ID3D11DepthStencilView *dsv;
    u32 depth_w, depth_h;
    std::vector<ID3D11Buffer *> vb;
    std::vector<u32> nv;
} g_gpu;
std::vector<std::vector<V3>> g_tri; // each mesh's triangles in editor axes, for picking

const char kHlsl[] =
    "cbuffer C : register(b0) { row_major float4x4 vp; float4 off; float4 tint; float4 light; };\n"
    "Texture2D t : register(t0); SamplerState s : register(s0);\n"
    "struct I { float3 p : POSITION; float3 n : NORMAL; float2 uv : TEXCOORD0; float4 c : COLOR0; };\n"
    "struct O { float4 p : SV_Position; float3 n : NORMAL; float2 uv : TEXCOORD0; float4 c : COLOR0; };\n"
    "O vs_main(I i) { O o; o.p = mul(float4(i.p + off.xyz, 1), vp); o.n = i.n; o.uv = i.uv; o.c = i.c; return o; }\n"
    "float4 ps_main(O i, bool front : SV_IsFrontFace) : SV_Target {\n"
    "  float4 c = t.Sample(s, i.uv) * i.c; if (c.a < 0.5) discard;\n"
    "  float3 n = normalize(i.n); float k = 0.45 + 0.55 * abs(dot(n, -light.xyz));\n"
    "  float3 rgb = c.rgb * k; rgb = lerp(rgb, tint.rgb, tint.a); return float4(rgb, 1); }\n";

struct Cb {
    float vp[4][4];
    float off[4], tint[4], light[4];
};

template <class T> void release(T *&p)
{
    if (p) p->Release();
    p = nullptr;
}

// ---- Direct3D resources ----
using CompileFn = HRESULT(WINAPI *)(LPCVOID, SIZE_T, LPCSTR, const D3D_SHADER_MACRO *, ID3DInclude *, LPCSTR, LPCSTR, UINT, UINT,
                                    ID3DBlob **, ID3DBlob **);

ID3DBlob *compile(CompileFn fn, const char *entry, const char *target)
{
    ID3DBlob *code = nullptr, *err = nullptr;
    if (FAILED(fn(kHlsl, sizeof kHlsl - 1, "level_edit", nullptr, nullptr, entry, target, 0, 0, &code, &err))) {
        port_log("level editor: shader %s failed: %s\n", entry, err ? static_cast<const char *>(err->GetBufferPointer()) : "?");
        release(err);
        return nullptr;
    }
    release(err);
    return code;
}

// textures packed into one atlas by rows (Saturn textures are small); a white block first for untextured polygons
struct Place {
    u32 x, y, w, h;
};
bool gpu_atlas(std::vector<Place> &place)
{
    const u32 A = 2048;
    std::vector<u32> px(static_cast<size_t>(A) * A, 0xffffffffu);
    u32 x = 8, y = 0, row = 8;
    place.resize(g_sc.ntex);
    for (u32 i = 0; i < g_sc.ntex; i++) {
        const LevelTex &t = g_sc.tex[i];
        if (t.w > A || t.h > A) return false;
        if (x + t.w > A) x = 0, y += row, row = 0;
        if (y + t.h > A) {
            port_log("level editor: the textures do not fit one %ux%u atlas\n", A, A);
            return false;
        }
        place[i] = {x, y, t.w, t.h};
        for (u32 r = 0; r < t.h; r++) std::memcpy(&px[static_cast<size_t>(y + r) * A + x], t.rgba + static_cast<size_t>(r) * t.w, t.w * 4u);
        x += t.w + 1, row = std::max(row, t.h + 1);
    }
    D3D11_TEXTURE2D_DESC d{};
    d.Width = d.Height = A;
    d.MipLevels = d.ArraySize = 1;
    d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_IMMUTABLE;
    d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA s{px.data(), A * 4u, 0};
    if (FAILED(g_gfx.dev->CreateTexture2D(&d, &s, &g_gpu.atlas))) return false;
    return SUCCEEDED(g_gfx.dev->CreateShaderResourceView(g_gpu.atlas, nullptr, &g_gpu.atlas_srv));
}

bool gpu_open()
{
    HMODULE dll = LoadLibraryW(L"d3dcompiler_47.dll");
    CompileFn fn = dll ? reinterpret_cast<CompileFn>(reinterpret_cast<void *>(GetProcAddress(dll, "D3DCompile"))) : nullptr;
    if (!fn) return false;
    ID3DBlob *vs = compile(fn, "vs_main", "vs_4_0"), *ps = compile(fn, "ps_main", "ps_4_0");
    bool ok = vs && ps && SUCCEEDED(g_gfx.dev->CreateVertexShader(vs->GetBufferPointer(), vs->GetBufferSize(), nullptr, &g_gpu.vs)) &&
              SUCCEEDED(g_gfx.dev->CreatePixelShader(ps->GetBufferPointer(), ps->GetBufferSize(), nullptr, &g_gpu.ps));
    if (ok) {
        D3D11_INPUT_ELEMENT_DESC el[] = {
            {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
            {"NORMAL", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 12, D3D11_INPUT_PER_VERTEX_DATA, 0},
            {"TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 24, D3D11_INPUT_PER_VERTEX_DATA, 0},
            {"COLOR", 0, DXGI_FORMAT_R8G8B8A8_UNORM, 0, 32, D3D11_INPUT_PER_VERTEX_DATA, 0},
        };
        ok = SUCCEEDED(g_gfx.dev->CreateInputLayout(el, 4, vs->GetBufferPointer(), vs->GetBufferSize(), &g_gpu.layout));
    }
    release(vs);
    release(ps);
    if (!ok) return false;
    D3D11_BUFFER_DESC b{};
    b.ByteWidth = sizeof(Cb);
    b.Usage = D3D11_USAGE_DYNAMIC;
    b.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    b.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    D3D11_SAMPLER_DESC sd{};
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    D3D11_RASTERIZER_DESC rd{};
    rd.FillMode = D3D11_FILL_SOLID;
    rd.CullMode = D3D11_CULL_NONE;
    rd.DepthClipEnable = TRUE;
    rd.ScissorEnable = TRUE;
    D3D11_DEPTH_STENCIL_DESC dd{};
    dd.DepthEnable = TRUE;
    dd.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
    dd.DepthFunc = D3D11_COMPARISON_GREATER_EQUAL; // reversed depth: see cam_matrices
    D3D11_BLEND_DESC bd{};
    bd.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    if (FAILED(g_gfx.dev->CreateBuffer(&b, nullptr, &g_gpu.cb)) || FAILED(g_gfx.dev->CreateSamplerState(&sd, &g_gpu.sampler)) ||
        FAILED(g_gfx.dev->CreateRasterizerState(&rd, &g_gpu.raster)) || FAILED(g_gfx.dev->CreateDepthStencilState(&dd, &g_gpu.depth_on)) ||
        FAILED(g_gfx.dev->CreateBlendState(&bd, &g_gpu.blend)))
        return false;
    std::vector<Place> place;
    if (!gpu_atlas(place)) return false;
    // each mesh's triangles: editor axes, a face normal per triangle, atlas coordinates
    g_tri.assign(g_sc.nmesh, {});
    g_gpu.vb.assign(g_sc.nmesh, nullptr);
    g_gpu.nv.assign(g_sc.nmesh, 0);
    for (u32 m = 0; m < g_sc.nmesh; m++) {
        const LevelMesh &mesh = g_sc.mesh[m];
        std::vector<GpuVert> out;
        for (u32 i = 0; i + 2 < mesh.nv; i += 3) {
            V3 p[3];
            for (int k = 0; k < 3; k++) {
                const LevelVert &v = mesh.v[i + static_cast<u32>(k)];
                p[k] = to_world_f(v.x, v.y, v.z);
                g_tri[m].push_back(p[k]);
            }
            V3 n = norm(cross(p[1] - p[0], p[2] - p[0]));
            for (int k = 0; k < 3; k++) {
                const LevelVert &v = mesh.v[i + static_cast<u32>(k)];
                float u = 2.0f / 2048.0f, w = 2.0f / 2048.0f; // inside the white block
                if (v.tex >= 0 && static_cast<u32>(v.tex) < place.size()) {
                    const Place &pl = place[static_cast<u32>(v.tex)];
                    float fu = std::fmin(std::fmax(v.u, 0.0f), 1.0f), fv = std::fmin(std::fmax(v.v, 0.0f), 1.0f);
                    u = (static_cast<float>(pl.x) + 0.5f + fu * (static_cast<float>(pl.w) - 1.0f)) / 2048.0f;
                    w = (static_cast<float>(pl.y) + 0.5f + fv * (static_cast<float>(pl.h) - 1.0f)) / 2048.0f;
                }
                out.push_back({p[k].x, p[k].y, p[k].z, n.x, n.y, n.z, u, w, v.rgba});
            }
        }
        if (out.empty()) continue;
        D3D11_BUFFER_DESC vd{};
        vd.ByteWidth = static_cast<UINT>(out.size() * sizeof(GpuVert));
        vd.Usage = D3D11_USAGE_IMMUTABLE;
        vd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
        D3D11_SUBRESOURCE_DATA s{out.data(), 0, 0};
        if (FAILED(g_gfx.dev->CreateBuffer(&vd, &s, &g_gpu.vb[m]))) return false;
        g_gpu.nv[m] = static_cast<u32>(out.size());
    }
    return true;
}

void gpu_close()
{
    for (ID3D11Buffer *&b : g_gpu.vb) release(b);
    g_gpu.vb.clear();
    g_gpu.nv.clear();
    g_tri.clear();
    release(g_gpu.vs), release(g_gpu.ps), release(g_gpu.layout), release(g_gpu.cb), release(g_gpu.sampler);
    release(g_gpu.raster), release(g_gpu.depth_on), release(g_gpu.blend), release(g_gpu.atlas_srv), release(g_gpu.atlas);
    release(g_gpu.dsv), release(g_gpu.depth);
    g_gpu.depth_w = g_gpu.depth_h = 0;
}

bool gpu_depth(u32 w, u32 h)
{
    if (g_gpu.dsv && g_gpu.depth_w == w && g_gpu.depth_h == h) return true;
    release(g_gpu.dsv), release(g_gpu.depth);
    D3D11_TEXTURE2D_DESC d{};
    d.Width = w, d.Height = h;
    d.MipLevels = d.ArraySize = 1;
    d.Format = DXGI_FORMAT_D32_FLOAT;
    d.SampleDesc.Count = 1;
    d.BindFlags = D3D11_BIND_DEPTH_STENCIL;
    if (FAILED(g_gfx.dev->CreateTexture2D(&d, nullptr, &g_gpu.depth)) ||
        FAILED(g_gfx.dev->CreateDepthStencilView(g_gpu.depth, nullptr, &g_gpu.dsv)))
        return false;
    g_gpu.depth_w = w, g_gpu.depth_h = h;
    return true;
}

void gpu_draw(ID3D11RenderTargetView *rtv)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    if (!gpu_depth(g_gfx.out_w, g_gfx.out_h)) return;
    cx->ClearDepthStencilView(g_gpu.dsv, D3D11_CLEAR_DEPTH, 0.0f, 0); // reversed depth: 0 is the farthest
    cx->OMSetRenderTargets(1, &rtv, g_gpu.dsv);
    D3D11_VIEWPORT vp{g_vx, g_vy, g_vw, g_vh, 0.0f, 1.0f};
    D3D11_RECT sc{static_cast<LONG>(g_vx), static_cast<LONG>(g_vy), static_cast<LONG>(g_vx + g_vw), static_cast<LONG>(g_vy + g_vh)};
    cx->RSSetViewports(1, &vp);
    cx->RSSetScissorRects(1, &sc);
    cx->RSSetState(g_gpu.raster);
    cx->OMSetDepthStencilState(g_gpu.depth_on, 0);
    float bf[4] = {0, 0, 0, 0};
    cx->OMSetBlendState(g_gpu.blend, bf, 0xffffffffu);
    cx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    cx->IASetInputLayout(g_gpu.layout);
    cx->VSSetShader(g_gpu.vs, nullptr, 0);
    cx->PSSetShader(g_gpu.ps, nullptr, 0);
    cx->PSSetSamplers(0, 1, &g_gpu.sampler);
    cx->PSSetShaderResources(0, 1, &g_gpu.atlas_srv);
    cx->VSSetConstantBuffers(0, 1, &g_gpu.cb);
    cx->PSSetConstantBuffers(0, 1, &g_gpu.cb);
    V3 L = light_world_dir();
    for (u32 i = 0; i < g_sc.nent; i++) {
        const LevelEnt &e = g_sc.ent[i];
        if (e.mesh < 0 || static_cast<u32>(e.mesh) >= g_gpu.vb.size() || !g_gpu.vb[static_cast<u32>(e.mesh)]) continue;
        D3D11_MAPPED_SUBRESOURCE m;
        if (FAILED(cx->Map(g_gpu.cb, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) return;
        Cb *c = static_cast<Cb *>(m.pData);
        std::memcpy(c->vp, g_vp.m, sizeof c->vp);
        V3 o = ent_world(e);
        c->off[0] = o.x, c->off[1] = o.y, c->off[2] = o.z, c->off[3] = 0;
        float a = g_sel[i] ? 0.35f : static_cast<int>(i) == g_hover_ent ? 0.15f : 0.0f;
        c->tint[0] = 1.0f, c->tint[1] = 0.62f, c->tint[2] = 0.12f, c->tint[3] = a;
        c->light[0] = L.x, c->light[1] = L.y, c->light[2] = L.z, c->light[3] = 0;
        cx->Unmap(g_gpu.cb, 0);
        UINT stride = sizeof(GpuVert), off = 0;
        cx->IASetVertexBuffers(0, 1, &g_gpu.vb[static_cast<u32>(e.mesh)], &stride, &off);
        cx->Draw(g_gpu.nv[static_cast<u32>(e.mesh)], 0);
    }
    D3D11_RECT all{0, 0, static_cast<LONG>(g_gfx.out_w), static_cast<LONG>(g_gfx.out_h)};
    cx->RSSetScissorRects(1, &all);
}

// ---- the editor's picture as a BMP (--level-edit-shot: tests and the report) ----
void shot(ID3D11RenderTargetView *rtv, const char *path)
{
    ID3D11Resource *res = nullptr;
    ID3D11Texture2D *tex = nullptr, *cpu = nullptr;
    rtv->GetResource(&res);
    if (!res || FAILED(res->QueryInterface(__uuidof(ID3D11Texture2D), reinterpret_cast<void **>(&tex)))) return release(res);
    D3D11_TEXTURE2D_DESC d;
    tex->GetDesc(&d);
    d.Usage = D3D11_USAGE_STAGING, d.BindFlags = 0, d.CPUAccessFlags = D3D11_CPU_ACCESS_READ, d.MiscFlags = 0;
    D3D11_MAPPED_SUBRESOURCE m;
    FILE *f = nullptr;
    if (SUCCEEDED(g_gfx.dev->CreateTexture2D(&d, nullptr, &cpu))) {
        g_gfx.ctx->CopyResource(cpu, tex);
        if (SUCCEEDED(g_gfx.ctx->Map(cpu, 0, D3D11_MAP_READ, 0, &m))) {
            if (fopen_s(&f, path, "wb") == 0 && f) {
                u32 w = d.Width, h = d.Height, size = 54 + w * h * 4;
                unsigned char hd[54] = {'B', 'M'};
                auto put = [&](int at, u32 v) {
                    for (int k = 0; k < 4; k++) hd[at + k] = static_cast<unsigned char>(v >> (8 * k));
                };
                put(2, size), put(10, 54), put(14, 40), put(18, w), put(22, static_cast<u32>(-static_cast<s32>(h)));
                hd[26] = 1, hd[28] = 32, put(34, w * h * 4);
                fwrite(hd, 1, 54, f);
                bool bgra = d.Format == DXGI_FORMAT_B8G8R8A8_UNORM || d.Format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
                std::vector<unsigned char> row(static_cast<size_t>(w) * 4);
                for (u32 y = 0; y < h; y++) {
                    const unsigned char *s = static_cast<const unsigned char *>(m.pData) + static_cast<size_t>(y) * m.RowPitch;
                    for (u32 x = 0; x < w; x++) {
                        row[4 * x + 0] = s[4 * x + (bgra ? 0 : 2)], row[4 * x + 1] = s[4 * x + 1], row[4 * x + 2] = s[4 * x + (bgra ? 2 : 0)];
                        row[4 * x + 3] = 255;
                    }
                    fwrite(row.data(), 1, row.size(), f);
                }
                fclose(f);
                game_log_line("level editor: picture %ux%u written to %s", w, h, path);
            }
            g_gfx.ctx->Unmap(cpu, 0);
        }
    }
    release(cpu), release(tex), release(res);
}

} // namespace level_ed
