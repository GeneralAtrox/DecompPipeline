"""The renderer's translucent modifier volumes (port/pvr/pvr_oit.hlsl ps_gather, ps_volume, ps_merge, ps_resolve; the
order pvr_modvol.c draws them in): a closed inclusion volume scales the shadow-bit translucent fragments it encloses
by the cheap shadow intensity, and leaves alone a fragment without the shadow bit, one in front of the volume and one
outside its outline. The kit's shaders run on the WARP rasterizer over a 4x4 target (the 640x480 screen scaled)."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))
import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

SHADERS = [("vs_5_0", "vs_main", "g_vs", "pvr.hlsl"), ("ps_5_0", "ps_gather", "g_gather", "pvr_oit.hlsl"),
           ("ps_5_0", "ps_volume", "g_volume", "pvr_oit.hlsl"), ("ps_5_0", "ps_merge", "g_merge", "pvr_oit.hlsl"),
           ("vs_5_0", "vs_full", "g_full", "pvr_oit.hlsl"), ("ps_5_0", "ps_resolve", "g_resolve", "pvr_oit.hlsl")]

# Rows 0-1: a white translucent quad with the shadow bit at 1/w 0.5 (batch 0); rows 2-3: the same without the shadow
# bit (batch 1), except column 3, a shadow-bit quad at 1/w 1.5, in front of the volume (batch 0). The volume covers
# columns 0-1 (x 0..320): its near face at 1/w 1.0 (batch 2, instruction 0), its far face at 0.25 (batch 3, the
# last triangles, instruction 1: inclusion), and column 3 too (x 480..640): it encloses rows 0-1 there, not the
# fragment in front.
HARNESS_C = r"""
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <stdio.h>
#include <string.h>
#include "shaders.h"

typedef struct { float x, y, z, u, v; unsigned base, off, batch; } Vtx;
typedef struct { unsigned isp, tsp, tcw, flags; } Batch;
typedef struct { float depth_scale, fog_density, punch_ref, shadow_alpha, fog_color[4]; unsigned fog_table[128];
                 float wide[4]; } Frame;

static ID3D11Device *dev;
static ID3D11DeviceContext *cx;

static int quad(Vtx *v, float x0, float y0, float x1, float y1, float z, unsigned batch)
{
    float p[6][2] = {{x0, y0}, {x1, y0}, {x0, y1}, {x1, y0}, {x1, y1}, {x0, y1}};
    for (int i = 0; i < 6; i++) {
        memset(&v[i], 0, sizeof v[i]);
        v[i].x = p[i][0]; v[i].y = p[i][1]; v[i].z = z; v[i].base = 0xffffffffu; v[i].batch = batch;
    }
    return 6;
}

static ID3D11Buffer *buffer(UINT bind, UINT misc, UINT stride, UINT size, const void *data)
{
    D3D11_BUFFER_DESC d; D3D11_SUBRESOURCE_DATA s; ID3D11Buffer *b = NULL;
    memset(&d, 0, sizeof d); memset(&s, 0, sizeof s);
    d.ByteWidth = size; d.BindFlags = bind; d.MiscFlags = misc; d.StructureByteStride = stride;
    s.pSysMem = data;
    if (FAILED(ID3D11Device_CreateBuffer(dev, &d, data ? &s : NULL, &b))) { printf("buffer\n"); ExitProcess(2); }
    return b;
}

static ID3D11Texture2D *texture(DXGI_FORMAT f, UINT bind, D3D11_USAGE use, UINT cpu)
{
    D3D11_TEXTURE2D_DESC d; ID3D11Texture2D *t = NULL;
    memset(&d, 0, sizeof d);
    d.Width = 4; d.Height = 4; d.MipLevels = 1; d.ArraySize = 1; d.Format = f; d.SampleDesc.Count = 1;
    d.Usage = use; d.BindFlags = bind; d.CPUAccessFlags = cpu;
    if (FAILED(ID3D11Device_CreateTexture2D(dev, &d, NULL, &t))) { printf("texture\n"); ExitProcess(2); }
    return t;
}

int main(void)
{
    static const D3D11_INPUT_ELEMENT_DESC el[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 12, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 0, DXGI_FORMAT_B8G8R8A8_UNORM, 0, 20, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 1, DXGI_FORMAT_B8G8R8A8_UNORM, 0, 24, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"BATCH", 0, DXGI_FORMAT_R32_UINT, 0, 28, D3D11_INPUT_PER_VERTEX_DATA, 0},
    };
    /* translucent: source alpha / inverse source alpha, alpha used, no fog (2); shadow bit = flags 8 */
    unsigned tsp = 4u << 29 | 5u << 26 | 2u << 22 | 1u << 20;
    Batch batches[4] = {{0, tsp, 0, 8u}, {0, tsp, 0, 0}, {0u << 29, 0, 0, 0}, {1u << 29, 0, 0, 0}};
    Frame fr;
    Vtx v[64];
    int n = 0, polys, near_end;
    ID3D11VertexShader *vs, *full; ID3D11PixelShader *gather, *volume, *merge, *resolve;
    ID3D11InputLayout *lay;
    ID3D11Texture2D *draw, *copy, *heads, *stage;
    ID3D11RenderTargetView *rtv; ID3D11UnorderedAccessView *uav[2]; ID3D11ShaderResourceView *srv[4];
    ID3D11Buffer *vb, *bb, *cb, *nodes;
    ID3D11RasterizerState *rs; ID3D11DepthStencilState *none;
    D3D11_RASTERIZER_DESC rd; D3D11_DEPTH_STENCIL_DESC dd; D3D11_UNORDERED_ACCESS_VIEW_DESC ud;
    D3D11_VIEWPORT vp = {0.0f, 0.0f, 4.0f, 4.0f, 0.0f, 1.0f};
    D3D11_MAPPED_SUBRESOURCE m;
    UINT stride = sizeof(Vtx), offset = 0, zero[2] = {0, 0}, endl[4] = {~0u, ~0u, ~0u, ~0u};
    float black[4] = {0, 0, 0, 1};

    if (FAILED(D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_WARP, NULL, 0, NULL, 0, D3D11_SDK_VERSION, &dev, NULL, &cx))) {
        printf("no WARP device\n");
        return 2;
    }
    n += quad(v + n, 0, 0, 640, 240, 0.5f, 0);
    n += quad(v + n, 0, 240, 480, 480, 0.5f, 1);
    n += quad(v + n, 480, 240, 640, 480, 1.5f, 0);
    polys = n;
    n += quad(v + n, 0, 0, 320, 480, 1.0f, 2);
    n += quad(v + n, 480, 0, 640, 480, 1.0f, 2);
    near_end = n;
    n += quad(v + n, 0, 0, 320, 480, 0.25f, 3);
    n += quad(v + n, 480, 0, 640, 480, 0.25f, 3);
    for (int i = polys; i < n; i++) v[i].base = 0;

    memset(&fr, 0, sizeof fr);
    fr.depth_scale = 0.5f; fr.punch_ref = 1.0f; fr.shadow_alpha = 0.5f; fr.wide[0] = 1.0f;
    vb = buffer(D3D11_BIND_VERTEX_BUFFER, 0, 0, (UINT)(n * sizeof(Vtx)), v);
    bb = buffer(D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, sizeof(Batch), sizeof batches,
                batches);
    cb = buffer(D3D11_BIND_CONSTANT_BUFFER, 0, 0, sizeof fr, &fr);
    nodes = buffer(D3D11_BIND_UNORDERED_ACCESS | D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 20,
                   20 * 256, NULL);
    draw = texture(DXGI_FORMAT_R8G8B8A8_UNORM, D3D11_BIND_RENDER_TARGET, D3D11_USAGE_DEFAULT, 0);
    copy = texture(DXGI_FORMAT_R8G8B8A8_UNORM, D3D11_BIND_SHADER_RESOURCE, D3D11_USAGE_DEFAULT, 0);
    stage = texture(DXGI_FORMAT_R8G8B8A8_UNORM, 0, D3D11_USAGE_STAGING, D3D11_CPU_ACCESS_READ);
    heads = texture(DXGI_FORMAT_R32_UINT, D3D11_BIND_UNORDERED_ACCESS | D3D11_BIND_SHADER_RESOURCE,
                    D3D11_USAGE_DEFAULT, 0);
    memset(&ud, 0, sizeof ud);
    ud.Format = DXGI_FORMAT_UNKNOWN; ud.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
    ud.Buffer.NumElements = 256; ud.Buffer.Flags = D3D11_BUFFER_UAV_FLAG_COUNTER;
    memset(&rd, 0, sizeof rd);
    rd.FillMode = D3D11_FILL_SOLID; rd.CullMode = D3D11_CULL_NONE; rd.DepthClipEnable = TRUE;
    memset(&dd, 0, sizeof dd);
    dd.DepthFunc = D3D11_COMPARISON_ALWAYS;
    if (FAILED(ID3D11Device_CreateRenderTargetView(dev, (ID3D11Resource *)draw, NULL, &rtv)) ||
        FAILED(ID3D11Device_CreateUnorderedAccessView(dev, (ID3D11Resource *)heads, NULL, &uav[0])) ||
        FAILED(ID3D11Device_CreateUnorderedAccessView(dev, (ID3D11Resource *)nodes, &ud, &uav[1])) ||
        FAILED(ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)bb, NULL, &srv[0])) ||
        FAILED(ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)heads, NULL, &srv[1])) ||
        FAILED(ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)nodes, NULL, &srv[2])) ||
        FAILED(ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)copy, NULL, &srv[3])) ||
        FAILED(ID3D11Device_CreateVertexShader(dev, g_vs, sizeof g_vs, NULL, &vs)) ||
        FAILED(ID3D11Device_CreateVertexShader(dev, g_full, sizeof g_full, NULL, &full)) ||
        FAILED(ID3D11Device_CreatePixelShader(dev, g_gather, sizeof g_gather, NULL, &gather)) ||
        FAILED(ID3D11Device_CreatePixelShader(dev, g_volume, sizeof g_volume, NULL, &volume)) ||
        FAILED(ID3D11Device_CreatePixelShader(dev, g_merge, sizeof g_merge, NULL, &merge)) ||
        FAILED(ID3D11Device_CreatePixelShader(dev, g_resolve, sizeof g_resolve, NULL, &resolve)) ||
        FAILED(ID3D11Device_CreateInputLayout(dev, el, 5, g_vs, sizeof g_vs, &lay)) ||
        FAILED(ID3D11Device_CreateRasterizerState(dev, &rd, &rs)) ||
        FAILED(ID3D11Device_CreateDepthStencilState(dev, &dd, &none))) {
        printf("resources\n");
        return 2;
    }
    ID3D11DeviceContext_ClearRenderTargetView(cx, rtv, black);
    ID3D11DeviceContext_ClearUnorderedAccessViewUint(cx, uav[0], endl);
    ID3D11DeviceContext_IASetInputLayout(cx, lay);
    ID3D11DeviceContext_IASetPrimitiveTopology(cx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_IASetVertexBuffers(cx, 0, 1, &vb, &stride, &offset);
    ID3D11DeviceContext_RSSetViewports(cx, 1, &vp);
    ID3D11DeviceContext_RSSetState(cx, rs);
    ID3D11DeviceContext_VSSetShader(cx, vs, NULL, 0);
    ID3D11DeviceContext_VSSetConstantBuffers(cx, 0, 1, &cb);
    ID3D11DeviceContext_PSSetConstantBuffers(cx, 0, 1, &cb);
    ID3D11DeviceContext_PSSetShaderResources(cx, 1, 1, &srv[0]);
    ID3D11DeviceContext_OMSetDepthStencilState(cx, none, 0);
    /* pvr_oit_begin, the translucent list, then pvr_modvol_trans_draw's order: each batch's faces, then at the
     * inclusion's last triangles the volume's triangles again */
    ID3D11DeviceContext_OMSetRenderTargetsAndUnorderedAccessViews(cx, 0, NULL, NULL, 0, 2, uav, zero);
    ID3D11DeviceContext_PSSetShader(cx, gather, NULL, 0);
    ID3D11DeviceContext_Draw(cx, (UINT)polys, 0);
    ID3D11DeviceContext_PSSetShader(cx, volume, NULL, 0);
    ID3D11DeviceContext_Draw(cx, (UINT)(near_end - polys), (UINT)polys);
    ID3D11DeviceContext_Draw(cx, (UINT)(n - near_end), (UINT)near_end);
    ID3D11DeviceContext_PSSetShader(cx, merge, NULL, 0);
    ID3D11DeviceContext_Draw(cx, (UINT)(n - polys), (UINT)polys);
    /* pvr_oit_resolve */
    ID3D11DeviceContext_OMSetRenderTargets(cx, 1, &rtv, NULL);
    ID3D11DeviceContext_CopyResource(cx, (ID3D11Resource *)copy, (ID3D11Resource *)draw);
    ID3D11DeviceContext_IASetInputLayout(cx, NULL);
    ID3D11DeviceContext_VSSetShader(cx, full, NULL, 0);
    ID3D11DeviceContext_PSSetShader(cx, resolve, NULL, 0);
    ID3D11DeviceContext_PSSetShaderResources(cx, 2, 3, &srv[1]);
    ID3D11DeviceContext_Draw(cx, 3, 0);
    ID3D11DeviceContext_CopyResource(cx, (ID3D11Resource *)stage, (ID3D11Resource *)draw);
    if (FAILED(ID3D11DeviceContext_Map(cx, (ID3D11Resource *)stage, 0, D3D11_MAP_READ, 0, &m))) return 2;
    for (int y = 0; y < 4; y++) {
        const unsigned char *row = (const unsigned char *)m.pData + y * m.RowPitch;
        for (int x = 0; x < 4; x++) printf("%d%c", row[x * 4], x == 3 ? '\n' : ' ');
    }
    ID3D11DeviceContext_Unmap(cx, (ID3D11Resource *)stage, 0);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class TranslucentModifierVolumes(unittest.TestCase):
    def test_inclusion_scales_enclosed_shadow_fragments(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=TOOL / "tests" / ".work" if (TOOL / "tests" / ".work").is_dir() else None))
        try:
            vc = str(game_config.vcvars32()).replace("/", "\\")
            steps = [f'fxc /nologo /O3 /WX /T {t} /E {e} /Vn {name} /Fh {name}.h "{KIT / "pvr" / src}"'
                     for t, e, name, src in SHADERS]
            (d / "shaders.h").write_text("".join(f'#include "{name}.h"\n' for _, _, name, _ in SHADERS),
                                         encoding="utf-8")
            (d / "mv.c").write_text(HARNESS_C, encoding="utf-8")
            steps.append("cl /nologo /TC /std:c11 /W4 /WX /O1 mv.c /Fe:mv.exe /link d3d11.lib")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {" && ".join(steps)}"', cwd=d, capture_output=True,
                               text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            run = subprocess.run([str(d / "mv.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout)
            # the red channel per pixel: enclosed shadow fragments at the cheap shadow intensity (0.5); the rest white
            self.assertEqual(run.stdout.split(), ["128", "128", "255", "128"] * 2 + ["255", "255", "255", "255"] * 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
