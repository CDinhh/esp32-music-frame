#include <Arduino.h>
#include <unity.h>
#include "validate.h"

void setUp() {}
void tearDown() {}

void test_sanitize() {
  TEST_ASSERT_EQUAL_STRING("cat.gif", sanitizeName("../../cat.gif").c_str());
  TEST_ASSERT_EQUAL_STRING("my_cat.gif", sanitizeName("my cat.GIF").c_str());
  TEST_ASSERT_EQUAL_STRING("", sanitizeName("../").c_str());
  TEST_ASSERT_EQUAL_STRING("", sanitizeName("...gif").c_str());
  TEST_ASSERT_EQUAL(32, (int)sanitizeName(std::string(100, 'a')).size());
}

void test_gif_header_and_dims() {
  uint8_t ok[] = {'G', 'I', 'F', '8', '9', 'a', 240, 0, 240, 0};
  uint8_t big[] = {'G', 'I', 'F', '8', '9', 'a', 0xE1, 1, 10, 0};  // 481 wide
  uint8_t png[] = {0x89, 'P', 'N', 'G', 0, 0, 0, 0, 0, 0};
  TEST_ASSERT_TRUE(isGifHeader(ok, 10));
  TEST_ASSERT_FALSE(isGifHeader(png, 10));
  TEST_ASSERT_FALSE(isGifHeader(ok, 3));
  TEST_ASSERT_TRUE(gifDimsOk(ok, 10));
  TEST_ASSERT_FALSE(gifDimsOk(big, 10));
}

void test_jpeg() {
  uint8_t j[] = {0xFF, 0xD8, 0xFF};
  TEST_ASSERT_TRUE(isJpeg(j, 3));
  TEST_ASSERT_FALSE(isJpeg(j + 1, 2));
}

void test_sources() {
  TEST_ASSERT_TRUE(validSources({"kugou", "lrclib"}));
  TEST_ASSERT_TRUE(validSources({}));
  TEST_ASSERT_FALSE(validSources({"spotify"}));
  TEST_ASSERT_FALSE(validSources({"lrclib", "lrclib"}));
}

void setup() {
  delay(500);
  UNITY_BEGIN();
  RUN_TEST(test_sanitize);
  RUN_TEST(test_gif_header_and_dims);
  RUN_TEST(test_jpeg);
  RUN_TEST(test_sources);
  UNITY_END();
}

void loop() {}
