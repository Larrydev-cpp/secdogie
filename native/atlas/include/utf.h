#pragma once

// UTF-8 <-> wchar conversion used by every platform path.
//
// JSON, UI ids, mapped paths and --find arguments are UTF-8 on the wire.
// Windows wchar_t is UTF-16; Linux/macOS wchar_t is UTF-32. Memory extract
// still stores hits as wstring; WideToUtf8 is the only printer.

#include <cstddef>
#include <cstdint>
#include <string>

namespace secdogie::atlas {

inline bool IsUtf16Surrogate(char32_t c) noexcept {
  return c >= 0xD800 && c <= 0xDFFF;
}

inline bool CjkCp(char32_t c) noexcept {
  return (c >= 0x3400 && c <= 0x9FFF) || (c >= 0xF900 && c <= 0xFAFF) ||
         (c >= 0x3040 && c <= 0x30FF) || (c >= 0xAC00 && c <= 0xD7AF) ||
         (c >= 0x3000 && c <= 0x303F);
}

inline bool LetterCp(char32_t c) noexcept {
  if ((c >= U'A' && c <= U'Z') || (c >= U'a' && c <= U'z')) return true;
  if (c >= 0x00C0 && c <= 0x024F) return true;
  if (c >= 0x0370 && c <= 0x03FF) return true;
  if (c >= 0x0400 && c <= 0x04FF) return true;
  if (CjkCp(c)) return true;
  return false;
}

inline bool PrintableCp(char32_t c) noexcept {
  if (c >= 0x20 && c < 0x7f) return true;
  if (c >= 0x00A0 && c <= 0x024F) return true;
  if (c >= 0x0370 && c <= 0x03FF) return true;
  if (c >= 0x0400 && c <= 0x04FF) return true;
  if (c >= 0x2000 && c <= 0x206F) {
    return c == 0x2013 || c == 0x2014 || c == 0x2018 || c == 0x2019 ||
           c == 0x201C || c == 0x201D || c == 0x2026;
  }
  if (CjkCp(c)) return true;
  if (c >= 0xFF00 && c <= 0xFFEF) return true;
  return false;
}

inline std::size_t Utf8Decode(const std::uint8_t* p, std::size_t n, char32_t* out) noexcept {
  if (!p || n == 0 || !out) return 0;
  const unsigned char c = p[0];
  if (c < 0x80) {
    *out = c;
    return 1;
  }
  if ((c & 0xE0) == 0xC0 && n >= 2) {
    const unsigned char c1 = p[1];
    if ((c1 & 0xC0) != 0x80) return 0;
    const char32_t cp = (static_cast<char32_t>(c & 0x1F) << 6) | (c1 & 0x3F);
    if (cp < 0x80) return 0;
    *out = cp;
    return 2;
  }
  if ((c & 0xF0) == 0xE0 && n >= 3) {
    const unsigned char c1 = p[1];
    const unsigned char c2 = p[2];
    if ((c1 & 0xC0) != 0x80 || (c2 & 0xC0) != 0x80) return 0;
    const char32_t cp = (static_cast<char32_t>(c & 0x0F) << 12) |
                        (static_cast<char32_t>(c1 & 0x3F) << 6) | (c2 & 0x3F);
    if (cp < 0x800 || IsUtf16Surrogate(cp)) return 0;
    *out = cp;
    return 3;
  }
  if ((c & 0xF8) == 0xF0 && n >= 4) {
    const unsigned char c1 = p[1];
    const unsigned char c2 = p[2];
    const unsigned char c3 = p[3];
    if ((c1 & 0xC0) != 0x80 || (c2 & 0xC0) != 0x80 || (c3 & 0xC0) != 0x80) return 0;
    const char32_t cp = (static_cast<char32_t>(c & 0x07) << 18) |
                        (static_cast<char32_t>(c1 & 0x3F) << 12) |
                        (static_cast<char32_t>(c2 & 0x3F) << 6) | (c3 & 0x3F);
    if (cp < 0x10000 || cp > 0x10FFFF) return 0;
    *out = cp;
    return 4;
  }
  return 0;
}

inline void AppendUtf8(std::string& o, char32_t cp) {
  if (cp < 0x80) {
    o.push_back(static_cast<char>(cp));
  } else if (cp < 0x800) {
    o.push_back(static_cast<char>(0xC0 | (cp >> 6)));
    o.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
  } else if (cp < 0x10000) {
    o.push_back(static_cast<char>(0xE0 | (cp >> 12)));
    o.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
    o.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
  } else if (cp <= 0x10FFFF) {
    o.push_back(static_cast<char>(0xF0 | (cp >> 18)));
    o.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
    o.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
    o.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
  }
}

inline void AppendWide(std::wstring& o, char32_t cp) {
#if defined(_WIN32)
  if (cp <= 0xFFFF) {
    o.push_back(static_cast<wchar_t>(cp));
  } else if (cp <= 0x10FFFF) {
    const char32_t u = cp - 0x10000;
    o.push_back(static_cast<wchar_t>(0xD800 + (u >> 10)));
    o.push_back(static_cast<wchar_t>(0xDC00 + (u & 0x3FF)));
  }
#else
  o.push_back(static_cast<wchar_t>(cp));
#endif
}

inline char32_t NextWide(const std::wstring& w, std::size_t& i) noexcept {
  if (i >= w.size()) return 0;
#if defined(_WIN32)
  const wchar_t c = w[i++];
  if (c >= 0xD800 && c <= 0xDBFF && i < w.size()) {
    const wchar_t d = w[i];
    if (d >= 0xDC00 && d <= 0xDFFF) {
      ++i;
      return 0x10000 + ((static_cast<char32_t>(c - 0xD800) << 10) |
                        static_cast<char32_t>(d - 0xDC00));
    }
  }
  return static_cast<char32_t>(static_cast<std::uint16_t>(c));
#else
  return static_cast<char32_t>(w[i++]);
#endif
}

inline std::string WideToUtf8(const std::wstring& w) {
  std::string o;
  o.reserve(w.size() * 3);
  for (std::size_t i = 0; i < w.size();) {
    AppendUtf8(o, NextWide(w, i));
  }
  return o;
}

inline std::wstring Utf8ToWide(const std::string& s) {
  std::wstring o;
  o.reserve(s.size());
  const auto* p = reinterpret_cast<const std::uint8_t*>(s.data());
  std::size_t i = 0;
  while (i < s.size()) {
    char32_t cp = 0;
    const std::size_t n = Utf8Decode(p + i, s.size() - i, &cp);
    if (n == 0) {
      AppendWide(o, 0xFFFD);
      ++i;
      continue;
    }
    AppendWide(o, cp);
    i += n;
  }
  return o;
}

}  // namespace secdogie::atlas
