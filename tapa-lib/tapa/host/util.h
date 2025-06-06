// Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
// All rights reserved. The contributor(s) of this file has/have agreed to the
// RapidStream Contributor License Agreement.

#ifndef TAPA_HOST_UTIL_H_
#define TAPA_HOST_UTIL_H_

#include <climits>

#include <iostream>

#include "tapa/base/util.h"

namespace tapa {

/// Obtain a value of type @c To by reinterpreting the object representation of
/// @c from.
///
/// @note       This function is slightly different from C++20 @c std::bit_cast.
/// @tparam To  Target type.
/// @param from Source object.
template <typename To, typename From>
inline typename std::enable_if<sizeof(To) == sizeof(From), To>::type  //
bit_cast(From from) noexcept {
  To to;
  std::memcpy(&to, &from, sizeof(To));
  return to;
}

template <typename T, size_t Depth = 1>
T reg(T x) {
  return x;
}

}  // namespace tapa

#endif  // TAPA_HOST_UTIL_H_
