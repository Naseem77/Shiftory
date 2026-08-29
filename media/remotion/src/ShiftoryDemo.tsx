import React from "react";
import {AbsoluteFill, interpolate, useCurrentFrame} from "remotion";
import {AmbientGraph} from "./components/AmbientGraph";
import {Chrome} from "./components/Chrome";
import {DURATION_IN_FRAMES, colors} from "./constants";
import {IncomingDiffScene} from "./scenes/IncomingDiffScene";
import {ProcessingScene} from "./scenes/ProcessingScene";
import {ResultScene} from "./scenes/ResultScene";

export const ShiftoryDemo: React.FC = () => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill style={{backgroundColor: colors.background}}>
      <AbsoluteFill
        style={{
          opacity: interpolate(
            frame,
            [0, 10, DURATION_IN_FRAMES - 15, DURATION_IN_FRAMES - 3],
            [0, 1, 1, 0],
            {extrapolateLeft: "clamp", extrapolateRight: "clamp"},
          ),
        }}
      >
        <AmbientGraph />
        <Chrome />
        <IncomingDiffScene />
        <ProcessingScene />
        <ResultScene />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
