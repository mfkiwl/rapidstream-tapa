// ==============================================================
// Generated by Vitis HLS v2024.1
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2024 Advanced Micro Devices, Inc. All Rights Reserved.
// ==============================================================

`timescale 1 ns / 1 ps

(* CORE_GENERATION_INFO="LowerLevelTask_LowerLevelTask,hls_ip_2024_1,{HLS_INPUT_TYPE=cxx,HLS_INPUT_FLOAT=0,HLS_INPUT_FIXED=0,HLS_INPUT_PART=xcu250-figd2104-2L-e,HLS_INPUT_CLOCK=3.333000,HLS_INPUT_ARCH=others,HLS_SYN_CLOCK=0.000000,HLS_SYN_LAT=0,HLS_SYN_TPT=none,HLS_SYN_MEM=0,HLS_SYN_DSP=0,HLS_SYN_FF=1,HLS_SYN_LUT=0,HLS_VERSION=2024_1}" *)

module LowerLevelTask (
        ap_clk,
        ap_rst_n,
        ap_start,
        ap_done,
        ap_idle,
        ap_ready,
        istream_s_dout,
        istream_s_empty_n,
        istream_s_read,
        istream_peek_dout,
        istream_peek_empty_n,
        istream_peek_read,
        ostreams_s_din,
        ostreams_s_full_n,
        ostreams_s_write,
        ostreams_peek,
        scalar
);

parameter    ap_ST_fsm_state1 = 1'd1;

input   ap_clk;
input   ap_rst_n;
input   ap_start;
output   ap_done;
output   ap_idle;
output   ap_ready;
input  [64:0] istream_s_dout;
input   istream_s_empty_n;
output   istream_s_read;
input  [64:0] istream_peek_dout;
input   istream_peek_empty_n;
output   istream_peek_read;
output  [64:0] ostreams_s_din;
input   ostreams_s_full_n;
output   ostreams_s_write;
input  [64:0] ostreams_peek;
input  [31:0] scalar;

reg ap_done;
reg ap_idle;
reg ap_ready;

 reg    ap_rst_n_inv;
(* fsm_encoding = "none" *) reg   [0:0] ap_CS_fsm;
wire    ap_CS_fsm_state1;
reg   [0:0] ap_NS_fsm;
reg    ap_ST_fsm_state1_blk;
wire    ap_ce_reg;

// power-on initialization
initial begin
#0 ap_CS_fsm = 1'd1;
end

always @ (posedge ap_clk) begin
    if (ap_rst_n_inv == 1'b1) begin
        ap_CS_fsm <= ap_ST_fsm_state1;
    end else begin
        ap_CS_fsm <= ap_NS_fsm;
    end
end

always @ (*) begin
    if ((ap_start == 1'b0)) begin
        ap_ST_fsm_state1_blk = 1'b1;
    end else begin
        ap_ST_fsm_state1_blk = 1'b0;
    end
end

always @ (*) begin
    if (((ap_start == 1'b1) & (1'b1 == ap_CS_fsm_state1))) begin
        ap_done = 1'b1;
    end else begin
        ap_done = 1'b0;
    end
end

always @ (*) begin
    if (((ap_start == 1'b0) & (1'b1 == ap_CS_fsm_state1))) begin
        ap_idle = 1'b1;
    end else begin
        ap_idle = 1'b0;
    end
end

always @ (*) begin
    if (((ap_start == 1'b1) & (1'b1 == ap_CS_fsm_state1))) begin
        ap_ready = 1'b1;
    end else begin
        ap_ready = 1'b0;
    end
end

always @ (*) begin
    case (ap_CS_fsm)
        ap_ST_fsm_state1 : begin
            ap_NS_fsm = ap_ST_fsm_state1;
        end
        default : begin
            ap_NS_fsm = 'bx;
        end
    endcase
end

assign ap_CS_fsm_state1 = ap_CS_fsm[32'd0];

always @ (*) begin
    ap_rst_n_inv = ~ap_rst_n;
end

assign istream_peek_read = 1'b0;

assign istream_s_read = 1'b0;

assign ostreams_s_din = 65'd0;

assign ostreams_s_write = 1'b0;

endmodule //LowerLevelTask
