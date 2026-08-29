import React from "react";
import {Composition} from "remotion";
import {DURATION_IN_FRAMES, FPS, HEIGHT, WIDTH} from "./constants";
import {ShiftoryDemo} from "./ShiftoryDemo";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="ShiftoryDemo"
      component={ShiftoryDemo}
      durationInFrames={DURATION_IN_FRAMES}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
    />
  );
};
