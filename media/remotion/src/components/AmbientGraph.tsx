import React from "react";
import {useCurrentFrame} from "remotion";
import {DURATION_IN_FRAMES, colors} from "../constants";

type Point = {
  x: number;
  y: number;
  radius: number;
  phase: number;
};

const makePoints = (): Point[] => {
  let seed = 741_103;
  const random = () => {
    seed = (seed * 16_807) % 2_147_483_647;
    return (seed - 1) / 2_147_483_646;
  };

  return Array.from({length: 24}, () => ({
    x: 40 + random() * 1200,
    y: 50 + random() * 620,
    radius: 1.4 + random() * 2.2,
    phase: random() * Math.PI * 2,
  }));
};

const points = makePoints();
const edges = points
  .map((_, index) => [index, (index * 7 + 5) % points.length] as const)
  .filter(([from, to]) => from !== to);

export const AmbientGraph: React.FC = () => {
  const frame = useCurrentFrame();
  const cycle = (frame / DURATION_IN_FRAMES) * Math.PI * 2;

  return (
    <>
      <div
        style={{
          position: "absolute",
          inset: 0,
          opacity: 0.2,
          backgroundImage:
            "linear-gradient(rgba(99,230,210,0.09) 1px, transparent 1px), linear-gradient(90deg, rgba(99,230,210,0.09) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
          backgroundPosition: `${(frame / DURATION_IN_FRAMES) * 48}px ${(frame / DURATION_IN_FRAMES) * 48}px`,
          maskImage:
            "radial-gradient(ellipse at 50% 45%, black 15%, transparent 76%)",
        }}
      />
      <svg
        width="1280"
        height="720"
        viewBox="0 0 1280 720"
        style={{position: "absolute", inset: 0, opacity: 0.42}}
      >
        {edges.map(([from, to]) => {
          const a = points[from];
          const b = points[to];
          return (
            <line
              key={`${from}-${to}`}
              x1={a.x + Math.sin(cycle + a.phase) * 6}
              y1={a.y + Math.cos(cycle + a.phase) * 4}
              x2={b.x + Math.sin(cycle + b.phase) * 6}
              y2={b.y + Math.cos(cycle + b.phase) * 4}
              stroke={colors.cyan}
              strokeOpacity="0.12"
              strokeWidth="1"
            />
          );
        })}
        {points.map((point, index) => {
          const pulse = 0.45 + 0.35 * Math.sin(cycle + point.phase);
          return (
            <circle
              key={index}
              cx={point.x + Math.sin(cycle + point.phase) * 6}
              cy={point.y + Math.cos(cycle + point.phase) * 4}
              r={point.radius}
              fill={index % 5 === 0 ? colors.amber : colors.cyan}
              opacity={pulse}
            />
          );
        })}
      </svg>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(circle at 18% 28%, rgba(99,230,210,0.08), transparent 32%), radial-gradient(circle at 82% 62%, rgba(255,191,105,0.07), transparent 30%)",
        }}
      />
    </>
  );
};
