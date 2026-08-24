#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unistd.h>

int main() {
  volatile char* heap = static_cast<volatile char*>(std::malloc(512));
  if (!heap) return 1;
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
  unsigned char dib[40] = {};
  dib[0] = 40;
  dib[4] = 64;
  dib[8] = 64;
  dib[12] = 1;
  dib[14] = 32;
  std::memcpy(const_cast<char*>(heap) + 256, dib, 40);
  std::printf("%d %p\n", static_cast<int>(getpid()), const_cast<char*>(heap));
  std::fflush(stdout);
  for (;;) {
    // Touch the buffer so the allocator cannot reclaim it.
    heap[0] = heap[0];
    pause();
  }
}
