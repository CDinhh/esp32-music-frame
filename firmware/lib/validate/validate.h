#pragma once
// Pure input checks (no Arduino deps) so they stay easy to test.
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

constexpr size_t GIF_MAX_BYTES = 2 * 1024 * 1024;
constexpr int GIF_MAX_DIM = 480;
constexpr size_t FRAME_MAX_BYTES = 100 * 1024;
inline const char* const SOURCE_IDS[] = {"ytmusic", "lrclib", "netease", "kugou", "musixmatch"};
constexpr size_t SOURCE_COUNT = sizeof(SOURCE_IDS) / sizeof(SOURCE_IDS[0]);

// "../My Cat.GIF" -> "My_Cat.gif"; returns "" if nothing usable is left.
inline std::string sanitizeName(const std::string& in) {
  std::string base = in.substr(in.find_last_of("/\\") == std::string::npos ? 0 : in.find_last_of("/\\") + 1);
  if (base.size() >= 4) {
    std::string ext = base.substr(base.size() - 4);
    for (auto& c : ext) c = (char)std::tolower((unsigned char)c);
    if (ext == ".gif") base.resize(base.size() - 4);
  }
  std::string out;
  for (char c : base) {
    if (std::isalnum((unsigned char)c) || c == '-' || c == '_') out += c;
    else if (c == ' ' || c == '.') out += '_';
  }
  while (!out.empty() && out[0] == '_') out.erase(0, 1);
  if (out.empty()) return "";
  if (out.size() > 28) out.resize(28);
  return out + ".gif";
}

inline bool isGifHeader(const uint8_t* b, size_t n) {
  return n >= 6 && b[0] == 'G' && b[1] == 'I' && b[2] == 'F' && b[3] == '8' && (b[4] == '7' || b[4] == '9') && b[5] == 'a';
}

inline bool gifDimsOk(const uint8_t* b, size_t n) {
  if (n < 10) return false;
  int w = b[6] | (b[7] << 8), h = b[8] | (b[9] << 8);
  return w > 0 && h > 0 && w <= GIF_MAX_DIM && h <= GIF_MAX_DIM;
}

inline bool isJpeg(const uint8_t* b, size_t n) { return n >= 2 && b[0] == 0xFF && b[1] == 0xD8; }

inline bool isSourceId(const std::string& s) {
  for (auto id : SOURCE_IDS) if (s == id) return true;
  return false;
}

// Only known ids, each at most once.
inline bool validSources(const std::vector<std::string>& v) {
  for (size_t i = 0; i < v.size(); i++) {
    if (!isSourceId(v[i])) return false;
    for (size_t j = 0; j < i; j++) if (v[j] == v[i]) return false;
  }
  return true;
}
