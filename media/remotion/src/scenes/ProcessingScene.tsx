import React from "react";
import {useCurrentFrame, useVideoConfig} from "remotion";
import {colors, mono, sans} from "../constants";
import {useGsapState} from "../gsap-state";

const command = "shiftory explain";
const metrics = [
  {label: "LINES", value: "12,000"},
  {label: "HUNKS", value: "184"},
  {label: "UNITS", value: "37"},
];

export const ProcessingScene: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const state = useGsapState(
    frame,
    fps,
    () => ({
      opacity: 0,
      y: 28,
      typed: 0,
      progress: 0,
      graph: 0,
      evidence: 0,
    }),
    (timeline, value) => {
      timeline
        .to(value, {opacity: 1, y: 0, duration: 0.55, ease: "power3.out"}, 2.2)
        .to(value, {typed: command.length, duration: 0.55, ease: "none"}, 2.35)
        .to(value, {progress: 1, duration: 2.0, ease: "power2.inOut"}, 2.75)
        .to(value, {graph: 1, duration: 0.8, ease: "power3.out"}, 3.15)
        .to(value, {evidence: 1, duration: 0.65, ease: "power3.out"}, 4.25)
        .to(value, {opacity: 0, y: -22, duration: 0.55, ease: "power2.in"}, 5.25);
    },
  );

  return (
    <div
      style={{
        position: "absolute",
        inset: "110px 70px 72px",
        opacity: state.opacity,
        translate: `0 ${state.y}px`,
      }}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.15fr 0.85fr",
          gap: 22,
          height: "100%",
        }}
      >
        <section
          style={{
            border: `1px solid ${colors.border}`,
            borderRadius: 18,
            backgroundColor: "rgba(9,13,17,0.96)",
            padding: "25px 28px",
            boxShadow: "0 30px 90px rgba(0,0,0,0.4)",
          }}
        >
          <div
            style={{
              color: colors.muted,
              fontFamily: mono,
              fontSize: 11,
              letterSpacing: "0.15em",
              marginBottom: 17,
            }}
          >
            TERMINAL / EVIDENCE PIPELINE
          </div>
          <div
            style={{
              height: 58,
              display: "flex",
              alignItems: "center",
              borderRadius: 10,
              backgroundColor: "#050709",
              border: "1px solid #1d2831",
              padding: "0 18px",
              color: colors.text,
              fontFamily: mono,
              fontSize: 18,
            }}
          >
            <span style={{color: colors.cyan, marginRight: 12}}>❯</span>
            {command.slice(0, Math.floor(state.typed))}
            <span
              style={{
                width: 9,
                height: 21,
                marginLeft: 4,
                backgroundColor: colors.cyan,
                opacity: state.typed < command.length ? 1 : 0.32,
              }}
            />
          </div>

          <div style={{marginTop: 27}}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                color: colors.muted,
                fontFamily: mono,
                fontSize: 11,
                marginBottom: 10,
              }}
            >
              <span>ACCOUNTING CHANGED SURFACE</span>
              <span style={{color: colors.cyan}}>{Math.round(state.progress * 100)}%</span>
            </div>
            <div
              style={{
                height: 6,
                borderRadius: 10,
                overflow: "hidden",
                backgroundColor: "#1a242c",
              }}
            >
              <div
                style={{
                  width: `${state.progress * 100}%`,
                  height: "100%",
                  borderRadius: 10,
                  background: `linear-gradient(90deg, ${colors.cyan}, ${colors.green})`,
                  boxShadow: `0 0 18px ${colors.cyan}55`,
                }}
              />
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: 11,
              marginTop: 25,
            }}
          >
            {metrics.map((metric, index) => {
              const active = state.progress > 0.2 + index * 0.2;
              return (
                <div
                  key={metric.label}
                  style={{
                    padding: "17px 16px",
                    border: `1px solid ${active ? "#31534e" : "#1b252d"}`,
                    borderRadius: 10,
                    backgroundColor: active ? "rgba(99,230,210,0.06)" : "#0b1015",
                    opacity: active ? 1 : 0.42,
                  }}
                >
                  <div
                    style={{
                      color: active ? colors.text : colors.muted,
                      fontFamily: sans,
                      fontWeight: 700,
                      fontSize: 24,
                    }}
                  >
                    {active ? metric.value : "—"}
                  </div>
                  <div
                    style={{
                      color: colors.muted,
                      fontFamily: mono,
                      fontSize: 10,
                      letterSpacing: "0.14em",
                      marginTop: 5,
                    }}
                  >
                    {metric.label}
                  </div>
                </div>
              );
            })}
          </div>

          <div
            style={{
              marginTop: 22,
              display: "grid",
              gridTemplateColumns: "20px 1fr auto",
              alignItems: "center",
              gap: 12,
              color: colors.muted,
              fontFamily: mono,
              fontSize: 12,
            }}
          >
            <span style={{color: state.evidence ? colors.green : colors.muted}}>
              {state.evidence ? "✓" : "·"}
            </span>
            <span>evidence packet / exact owners / stable citations</span>
            <span style={{color: state.evidence ? colors.green : colors.muted}}>
              {state.evidence ? "SEALED" : "PENDING"}
            </span>
          </div>
        </section>

        <section
          style={{
            border: `1px solid ${colors.border}`,
            borderRadius: 18,
            backgroundColor: "rgba(12,17,22,0.94)",
            padding: "25px 25px",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              color: colors.amber,
              fontFamily: mono,
              fontSize: 11,
              letterSpacing: "0.15em",
            }}
          >
            GRAPHORA / STRUCTURAL CONTEXT
          </div>
          <div
            style={{
              color: colors.text,
              fontFamily: sans,
              fontSize: 27,
              fontWeight: 660,
              lineHeight: 1.18,
              marginTop: 11,
            }}
          >
            Relationships enrich.
            <br />
            <span style={{color: colors.muted}}>Git decides.</span>
          </div>
          <svg
            width="420"
            height="225"
            viewBox="0 0 420 225"
            style={{
              marginLeft: -16,
              marginTop: 12,
              opacity: state.graph,
              scale: 0.9 + state.graph * 0.1,
            }}
          >
            <path
              d="M52 120 C110 120 116 52 180 62 S265 140 344 92"
              fill="none"
              stroke={colors.cyan}
              strokeOpacity="0.34"
              strokeWidth="2"
              strokeDasharray="6 7"
            />
            <path
              d="M52 120 C128 122 164 186 244 166 S308 102 344 92"
              fill="none"
              stroke={colors.amber}
              strokeOpacity="0.3"
              strokeWidth="2"
            />
            {[
              [52, 120, "diff", colors.coral],
              [180, 62, "symbol", colors.cyan],
              [244, 166, "caller", colors.amber],
              [344, 92, "evidence", colors.green],
            ].map(([x, y, label, color]) => (
              <g key={String(label)}>
                <circle cx={Number(x)} cy={Number(y)} r="21" fill="#0c1418" stroke={String(color)} />
                <circle cx={Number(x)} cy={Number(y)} r="5" fill={String(color)} />
                <text
                  x={Number(x)}
                  y={Number(y) + 39}
                  textAnchor="middle"
                  fill="#758691"
                  fontFamily={mono}
                  fontSize="10"
                >
                  {label}
                </text>
              </g>
            ))}
          </svg>
          <div
            style={{
              borderTop: `1px solid ${colors.border}`,
              paddingTop: 13,
              color: colors.muted,
              fontFamily: mono,
              fontSize: 11,
              lineHeight: 1.6,
            }}
          >
            structural hints&nbsp;&nbsp; <span style={{color: colors.amber}}>37 linked units</span>
            <br />
            authority&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; <span style={{color: colors.green}}>Git objects + patch</span>
          </div>
        </section>
      </div>
    </div>
  );
};
