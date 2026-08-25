#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <unistd.h>
#endif

// Planted CAD viewport: BITMAPFILEHEADER + 32bpp DIB whose pixels are the
// reconstructed drawing (extents box, hole, dimension ticks). Inspect reads
// this with ExtractDibs — it is not a screenshot and not a mock SVG.

static void PutBGRA(unsigned char* pix, int w, int h, int x, int y,
                    unsigned char r, unsigned char g, unsigned char b) {
  if (x < 0 || y < 0 || x >= w || y >= h) return;
  const int i = (y * w + x) * 4;
  pix[i + 0] = b;
  pix[i + 1] = g;
  pix[i + 2] = r;
  pix[i + 3] = 255;
}

static void HLine(unsigned char* pix, int w, int h, int x0, int x1, int y,
                  unsigned char r, unsigned char g, unsigned char b) {
  if (x1 < x0) {
    const int t = x0;
    x0 = x1;
    x1 = t;
  }
  for (int x = x0; x <= x1; ++x) PutBGRA(pix, w, h, x, y, r, g, b);
}

static void VLine(unsigned char* pix, int w, int h, int x, int y0, int y1,
                  unsigned char r, unsigned char g, unsigned char b) {
  if (y1 < y0) {
    const int t = y0;
    y0 = y1;
    y1 = t;
  }
  for (int y = y0; y <= y1; ++y) PutBGRA(pix, w, h, x, y, r, g, b);
}

static void RectStroke(unsigned char* pix, int w, int h, int x, int y, int rw, int rh,
                       unsigned char r, unsigned char g, unsigned char b) {
  HLine(pix, w, h, x, x + rw - 1, y, r, g, b);
  HLine(pix, w, h, x, x + rw - 1, y + rh - 1, r, g, b);
  VLine(pix, w, h, x, y, y + rh - 1, r, g, b);
  VLine(pix, w, h, x + rw - 1, y, y + rh - 1, r, g, b);
}

static void CircleStroke(unsigned char* pix, int w, int h, int cx, int cy, int rad,
                         unsigned char r, unsigned char g, unsigned char b) {
  int x = rad;
  int y = 0;
  int err = 0;
  while (x >= y) {
    PutBGRA(pix, w, h, cx + x, cy + y, r, g, b);
    PutBGRA(pix, w, h, cx + y, cy + x, r, g, b);
    PutBGRA(pix, w, h, cx - y, cy + x, r, g, b);
    PutBGRA(pix, w, h, cx - x, cy + y, r, g, b);
    PutBGRA(pix, w, h, cx - x, cy - y, r, g, b);
    PutBGRA(pix, w, h, cx - y, cy - x, r, g, b);
    PutBGRA(pix, w, h, cx + y, cy - x, r, g, b);
    PutBGRA(pix, w, h, cx + x, cy - y, r, g, b);
    if (err <= 0) {
      ++y;
      err += 2 * y + 1;
    }
    if (err > 0) {
      --x;
      err -= 2 * x + 1;
    }
  }
}

static void PaintCad(unsigned char* pix, int w, int h) {
  for (int y = 0; y < h; ++y) {
    for (int x = 0; x < w; ++x) {
      PutBGRA(pix, w, h, x, y, 14, 16, 20);
    }
  }
  RectStroke(pix, w, h, 0, 0, w, h, 200, 200, 196);
  // Layer DIMS strip (left) + extents box.
  for (int y = 6; y <= 14; ++y) HLine(pix, w, h, 8, 54, y, 143, 163, 140);
  RectStroke(pix, w, h, 18, 24, 124, 52, 197, 203, 212);
  CircleStroke(pix, w, h, 48, 50, 16, 197, 203, 212);
  CircleStroke(pix, w, h, 48, 50, 8, 143, 163, 140);
  HLine(pix, w, h, 48, 124, 50, 197, 203, 212);
  VLine(pix, w, h, 48, 34, 66, 197, 203, 212);
  RectStroke(pix, w, h, 86, 34, 44, 32, 197, 203, 212);
  // Dimension ticks under the plate (LAYER_DIMS).
  HLine(pix, w, h, 86, 129, 78, 143, 163, 140);
  VLine(pix, w, h, 86, 74, 82, 143, 163, 140);
  VLine(pix, w, h, 129, 74, 82, 143, 163, 140);
  HLine(pix, w, h, 18, 141, 86, 108, 112, 118);
}

static volatile char* PlantHeap() {
  constexpr int kBytes = 65536;
  volatile char* heap = static_cast<volatile char*>(std::malloc(kBytes));
  if (!heap) return nullptr;
  std::memset(const_cast<char*>(heap), 0, kBytes);
  const char k0[] = "SECDOGIE_TARGET_v1";
  const char k1[] = "Zoom Extents";
  const char k2[] = "LAYER_DIMS";
  const char k3[] = "{\"layer\":\"DIMS\",\"zoom\":12.5,\"viewport\":\"ext\"}";
  std::memcpy(const_cast<char*>(heap), k0, sizeof(k0));
  std::memcpy(const_cast<char*>(heap) + 32, k1, sizeof(k1));
  std::memcpy(const_cast<char*>(heap) + 64, k2, sizeof(k2));
  std::memcpy(const_cast<char*>(heap) + 96, k3, sizeof(k3));
  const unsigned char u16[] = {'I', 0, 'D', 0, '_', 0, 'Z', 0, 'O', 0, 'O', 0,
                               'M', 0, '_', 0, 'E', 0, 'X', 0, 'T', 0, 'E', 0,
                               'N', 0, 'T', 0, 'S', 0, 0, 0};
  std::memcpy(const_cast<char*>(heap) + 160, u16, sizeof(u16));
  const unsigned char cjk16[] = {0xFE, 0x56, 0x42, 0x5C, 0x3A, 0x5C, 0xF8, 0x5B, 0, 0};
  std::memcpy(const_cast<char*>(heap) + 200, cjk16, sizeof(cjk16));
  const unsigned char cjk8[] = {0xE5, 0x9B, 0xBE, 0xE5, 0xB1, 0x82,
                                0xE5, 0xB0, 0xBA, 0xE5, 0xAF, 0xB8, 0};
  std::memcpy(const_cast<char*>(heap) + 220, cjk8, sizeof(cjk8));

  constexpr std::int32_t kW = 160;
  constexpr std::int32_t kH = 96;
  constexpr std::uint32_t kOffBits = 54;
  constexpr std::uint32_t kFile = 14 + 40 + static_cast<std::uint32_t>(kW * kH * 4);
  unsigned char file[14] = {'B', 'M'};
  std::memcpy(file + 2, &kFile, 4);
  std::memcpy(file + 10, &kOffBits, 4);
  unsigned char hdr[40] = {};
  hdr[0] = 40;
  std::memcpy(hdr + 4, &kW, 4);
  std::memcpy(hdr + 8, &kH, 4);
  hdr[12] = 1;
  hdr[14] = 32;
  const std::uint32_t img = static_cast<std::uint32_t>(kW * kH * 4);
  std::memcpy(hdr + 20, &img, 4);
  constexpr int kBmp = 512;
  std::memcpy(const_cast<char*>(heap) + kBmp, file, 14);
  std::memcpy(const_cast<char*>(heap) + kBmp + 14, hdr, 40);
  unsigned char* pix =
      reinterpret_cast<unsigned char*>(const_cast<char*>(heap) + kBmp + 54);
  PaintCad(pix, kW, kH);
  heap[kBytes - 1] = heap[kBytes - 1];
  return heap;
}

#if defined(_WIN32)
static volatile char* g_heap = nullptr;

static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
  switch (msg) {
    case WM_CREATE:
      CreateWindowW(L"BUTTON", L"Zoom Extents",
                    WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 16, 16, 180, 32, hwnd,
                    reinterpret_cast<HMENU>(static_cast<uintptr_t>(1)),
                    GetModuleHandleW(nullptr), nullptr);
      CreateWindowW(L"BUTTON", L"LAYER_DIMS",
                    WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 16, 56, 180, 32, hwnd,
                    reinterpret_cast<HMENU>(static_cast<uintptr_t>(2)),
                    GetModuleHandleW(nullptr), nullptr);
      return 0;
    case WM_DESTROY:
      PostQuitMessage(0);
      return 0;
    default:
      break;
  }
  return DefWindowProcW(hwnd, msg, wparam, lparam);
}

int main() {
  g_heap = PlantHeap();
  if (!g_heap) return 1;

  WNDCLASSW wc{};
  wc.lpfnWndProc = WndProc;
  wc.hInstance = GetModuleHandleW(nullptr);
  wc.lpszClassName = L"SecDogieAtlasTarget";
  wc.hbrBackground = reinterpret_cast<HBRUSH>(static_cast<uintptr_t>(COLOR_WINDOW + 1));
  wc.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));
  RegisterClassW(&wc);

  HWND wnd = CreateWindowW(L"SecDogieAtlasTarget", L"atlas_target",
                           WS_OVERLAPPEDWINDOW | WS_VISIBLE, 80, 80, 340, 180,
                           nullptr, nullptr, wc.hInstance, nullptr);
  (void)wnd;
  std::printf("%u %p\n", GetCurrentProcessId(), const_cast<char*>(g_heap));
  std::fflush(stdout);

  MSG msg;
  while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
    g_heap[0] = g_heap[0];
    TranslateMessage(&msg);
    DispatchMessageW(&msg);
  }
  return 0;
}
#else
int main() {
  volatile char* heap = PlantHeap();
  if (!heap) return 1;
  std::printf("%d %p\n", static_cast<int>(getpid()), const_cast<char*>(heap));
  std::fflush(stdout);
  for (;;) {
    heap[0] = heap[0];
    pause();
  }
}
#endif
