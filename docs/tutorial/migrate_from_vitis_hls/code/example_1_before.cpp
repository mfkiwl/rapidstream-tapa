// Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
// All rights reserved. The contributor(s) of this file has/have agreed to the
// RapidStream Contributor License Agreement.

#include <hls_stream.h>
#include <hls_vector.h>
#include "assert.h"

#define MEMORY_DWIDTH 512
#define SIZEOF_WORD 4
#define NUM_WORDS ((MEMORY_DWIDTH) / (8 * SIZEOF_WORD))

#define DATA_SIZE 4096

void load_input(hls::vector<uint32_t, NUM_WORDS>* in,
                hls::stream<hls::vector<uint32_t, NUM_WORDS>>& inStream,
                int Size) {
  for (int i = 0; i < Size; i++) {
#pragma HLS pipeline II = 1
    inStream << in[i];
  }
}

void compute_add(hls::stream<hls::vector<uint32_t, NUM_WORDS>>& in1_stream,
                 hls::stream<hls::vector<uint32_t, NUM_WORDS>>& in2_stream,
                 hls::stream<hls::vector<uint32_t, NUM_WORDS>>& out_stream,
                 int Size) {
  for (int i = 0; i < Size; i++) {
#pragma HLS pipeline II = 1
    out_stream << (in1_stream.read() + in2_stream.read());
  }
}

void store_result(hls::vector<uint32_t, NUM_WORDS>* out,
                  hls::stream<hls::vector<uint32_t, NUM_WORDS>>& out_stream,
                  int Size) {
  for (int i = 0; i < Size; i++) {
#pragma HLS pipeline II = 1
    out[i] = out_stream.read();
  }
}

extern "C" {

void vadd(hls::vector<uint32_t, NUM_WORDS>* in1,
          hls::vector<uint32_t, NUM_WORDS>* in2,
          hls::vector<uint32_t, NUM_WORDS>* out, int size) {
#pragma HLS INTERFACE m_axi port = in1 bundle = gmem0
#pragma HLS INTERFACE m_axi port = in2 bundle = gmem1
#pragma HLS INTERFACE m_axi port = out bundle = gmem0

  hls::stream<hls::vector<uint32_t, NUM_WORDS>> in1_stream("input_stream_1");
  hls::stream<hls::vector<uint32_t, NUM_WORDS>> in2_stream("input_stream_2");
  hls::stream<hls::vector<uint32_t, NUM_WORDS>> out_stream("output_stream");

#pragma HLS dataflow
  load_input(in1, in1_stream, size);
  load_input(in2, in2_stream, size);
  compute_add(in1_stream, in2_stream, out_stream, size);
  store_result(out, out_stream, size);
}
}
