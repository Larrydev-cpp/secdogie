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

static volatile char* PlantHeap() {
  volatile char* heap = static_cast<volatile char*>(std::malloc(512));
  if (!heap) return nullptr;
  std::memset(const_cast<char*>(heap), 0, 512);
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
  // UTF-16LE 图层尺寸 — Windows / Cocoa wide layout
  const unsigned char cjk16[] = {0xFE, 0x56, 0x42, 0x5C, 0x3A, 0x5C, 0xF8, 0x5B, 0, 0};
  std::memcpy(const_cast<char*>(heap) + 200, cjk16, sizeof(cjk16));
  // UTF-8 图层尺寸 — Linux / macOS heap primary
  const unsigned char cjk8[] = {0xE5, 0x9B, 0xBE, 0xE5, 0xB1, 0x82,
                                0xE5, 0xB0, 0xBA, 0xE5, 0xAF, 0xB8, 0};
  std::memcpy(const_cast<char*>(heap) + 220, cjk8, sizeof(cjk8));
  unsigned char dib[40] = {};
  dib[0] = 40;
  dib[4] = 64;
  dib[8] = 64;
  dib[12] = 1;
  dib[14] = 32;
  std::memcpy(const_cast<char*>(heap) + 256, dib, 40);
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
  wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
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
