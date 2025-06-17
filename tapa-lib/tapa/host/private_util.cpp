// Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
// All rights reserved. The contributor(s) of this file has/have agreed to the
// RapidStream Contributor License Agreement.

#include <string>
#include <string_view>

namespace tapa::internal {

std::string StrCat(std::initializer_list<std::string_view> pieces) {
  size_t total_length = 0;
  for (std::string_view piece : pieces) total_length += piece.size();
  std::string text;
  text.reserve(total_length);
  for (std::string_view piece : pieces) text += piece;
  return text;
}

}  // namespace tapa::internal
