/*
 * Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
 * All rights reserved. The contributor(s) of this file has/have agreed to the
 * RapidStream Contributor License Agreement.
 */

"use strict";

import { getComboName } from "./helper.js";

/** @satisfies {HTMLElement | null} */
const container = document.querySelector(".graph-container");
if (container === null) {
  throw new TypeError("container is null!");
}

/** @type {import("@antv/g6").CanvasOptions} */
const canvasOptions = {
  container,
  autoResize: true,
};

/** @type {import("@antv/g6").ViewportOptions} */
const viewportOptions = {
  autoFit: {
    type: "view",
    options: { when: "overflow" },
    animation: { duration: 100 }, // ms
  },
  padding: 20, // px
  zoomRange: [0.1, 2.5], // %
};

/** @type {(id: string | undefined) => string | undefined} */
const trimEdgeId = id => {
  // Trim the prefix part
  id = id?.slice(id.indexOf("/") + 1);
  // If still very long, then cap each part's length to 15
  if (id && id.length > 20) {
    id = id.split("/").map(
      part => part.length <= 15 ? part : `${part.slice(1, 12)}...`
    ).join("/");
  }
  return id;
};

/** @type {Required<Pick<import("@antv/g6").GraphOptions, "node" | "edge" | "combo">>} */
const elementOptions = {
  // TODO: customize selected style
  // https://g6.antv.antgroup.com/api/elements/nodes/base-node
  node: {
    type: "rect",
    style: {
      size: ({ style }) => style?.size ?? [120, 40],
      fill: ({ style }) => style?.fill ?? "#198754",
      radius: 2,
      // TODO: port toggle
      portR: 5,
      portFill: "#198754",

      labelPlacement: "center",
      labelWordWrap: true, // enable label ellipsis
      labelMaxWidth: 100,
      labelMaxLines: 2,
      labelFill: "white",
      labelFontWeight: "bold",
      labelFontFamily: "monospace",
      labelText: ({ id }) => id.startsWith("<unknown>@")
        ? id.replace("<unknown>@", "<unknown> @ ")
        : id,
    },
    // Builtin states: "selected" "highlight" "active" "inactive" "disabled"
    state: {
      selected: {
        stroke: "#051b11",
        strokeOpacity: 0.75,
        lineWidth: 2,
        haloLineWidth: 10,
      },
    },
  },
  edge: {
    style: {
      stroke: "#A3CFBB",
      endArrow: true,

      labelBackground: true,
      labelBackgroundFill: "white",
      labelBackgroundFillOpacity: 0.75,
      labelBackgroundRadius: 1,
      labelFontFamily: "monospace",
      labelText: ({ id }) => trimEdgeId(id),
    },
    state: {
      selected: {
        labelFontWeight: "normal",
        labelFontSize: 12,
        labelBackgroundFill: "#EFE",
      }
    }
  },
  combo: {
    // type: "rect",
    style: {
      fill: "#13795B",
      stroke: "#13795B",
      haloStroke: "#13795B",
      collapsedFillOpacity: 0.75,
      collapsedMarkerFontSize: 16,
      halo: true,
      haloStrokeOpacity: 0,

      labelFill: "gray",
      labelPlacement: "top",
      labelText: ({ id }) => getComboName(id),
    },
    state: {
      selected: {
        haloStrokeOpacity: 0.25,
        lineWidth: 3,
        labelFontSize: 10,
      },
    },
  },
}

/**
 * @type      {import("@antv/g6").SingleLayoutOptions}
 * @satisfies {import("@antv/g6").AntVDagreLayoutOptions} */
 export const antvDagre = {
  type: "antv-dagre",
  // rankdir: "LR",
  ranksep: 60,
  nodeSize: [120, 40],
  nodesep: 20,
  // ranker: "network-simplex",
};

/**
 * @type      {import("@antv/g6").SingleLayoutOptions}
 * @satisfies {import("@antv/g6").DagreLayoutOptions} */
 export const dagre = {
  type: "dagre",
  nodeSize: [160, 120],
};

/** `force-atlas2` specifc config for large graph:
 * - `kr`:    much larger than kg
 * - `kg`:    much smaller than kr, larger than 1 to work with small graph
 * - `ks`:    speed, at least 0.01 * nodeSize
 * - `ksmax`: max speed, not very important
 * - `tao`:   10 is the sweet point
 * @type      {import("@antv/g6").SingleLayoutOptions}
 * @satisfies {import("@antv/g6").ForceAtlas2LayoutOptions} */
export const forceAtlas2 = {
  type: "force-atlas2",
  kr: 120,
  kg: 10,
  ks: 1,
  ksmax: 120,
  tao: 10,
  preventOverlap: true,
  nodeSize: [100, 40],
  preLayout: true,
};


export const layoutOptions = forceAtlas2;

/** @type {Omit<import("@antv/g6").GraphOptions, "data">} */
export const graphOptions = {
  ...canvasOptions,
  ...viewportOptions,
  ...elementOptions,

  theme: "light",

  animation: { duration: 250 }, // ms

  layout: layoutOptions,

  // behaviors are defined in graph.js since they call the graph object.

  plugins: [
    /** @type {import("@antv/g6").TooltipOptions} */
    ({
      type: "tooltip",
      getContent: (_event, items) => Promise.resolve(
        items.map(
          item => `ID: <code>${item.id}</code>`
        ).join("<br>")
      ),
    }),
  ],

  transforms: ["process-parallel-edges"],

};
