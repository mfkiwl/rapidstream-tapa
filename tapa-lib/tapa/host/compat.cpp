// Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
// All rights reserved. The contributor(s) of this file has/have agreed to the
// RapidStream Contributor License Agreement.

#include "tapa/host/compat.h"

namespace tapa::hls_compat {

task::task() { this->mode_override = 1; }

}  // namespace tapa::hls_compat
