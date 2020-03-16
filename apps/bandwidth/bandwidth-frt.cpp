#include <stdlib.h>

#include <frt.h>
#include <tlp.h>

#include "bandwidth.h"

void Bandwidth(tlp::async_mmaps<Elem, kBankCount> chan, uint64_t n) {
  auto instance = fpga::Instance(getenv("BITSTREAM"));
  for (int i = 0; i < kBankCount; ++i) {
    auto arg = fpga::ReadWrite(chan[i].get(), chan[i].size());
    instance.AllocBuf(i, arg);
    instance.SetArg(i, arg);
  }
  instance.SetArg(kBankCount, n);

  instance.WriteToDevice();
  instance.Exec();
  instance.ReadFromDevice();
  instance.Finish();

  LOG(INFO) << "kernel time: " << instance.ComputeTimeSeconds() * 1e3 << " ms";
  double size = 0.;
  for (int i = 0; i < kBankCount; ++i) {
    size += chan[i].size();
  }
  LOG(INFO) << "throughput: "
            << size * 2 * sizeof(Elem) / instance.ComputeTimeNanoSeconds()
            << " GB/s";
}