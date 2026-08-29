import React from "react";
import {gsap} from "gsap";
import {useCurrentFrame, useVideoConfig} from "remotion";
import {colors, mono, sans} from "../constants";
import {useGsapState} from "../gsap-state";

const diffLines = [
  {prefix: "@@", text: "@@ scheduler.assign_shift()", color: colors.blue},
  {prefix: "-", text: "worker = available[0]", color: colors.coral},
  {prefix: "+", text: "worker = choose_by_context(queue)", color: colors.green},
  {prefix: "+", text: "assignment.actor = request.actor", color: colors.green},
  {prefix: "+", text: "emit_shift_event(assignment)", color: colors.green},
  {prefix: "@@", text: "@@ api/queue.py: enqueue()", color: colors.blue},
  {prefix: "-", text: "queue.push(payload)", color: colors.coral},
  {prefix: "+", text: "queue.push(payload, actor=actor)", color: colors.green},
  {prefix: "+", text: "trace.link(request_id, shift_id)", color: colors.green},
];

export const IncomingDiffScene: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const state = useGsapState(
    frame,
    fps,
    () => ({opacity: 0, y: 34, count: 0, stream: 0, pressure: 0}),
    (timeline, value) => {
      timeline
        .to(value, {opacity: 1, y: 0, duration: 0.65, ease: "power3.out"}, 0.2)
        .to(value, {count: 12_000, duration: 1.8, ease: "power3.inOut"}, 0.35)
        .to(value, {stream: 1, duration: 1.8, ease: "power2.inOut"}, 0.45)
        .to(
          value,
          {pressure: 1, duration: 0.45, ease: "power2.out", yoyo: true, repeat: 3},
          0.65,
        )
        .to(value, {opacity: 0, y: -24, duration: 0.5, ease: "power2.in"}, 2.48);
    },
  );
  const visibleRows = Math.max(1, Math.ceil(state.stream * diffLines.length));

  return (
    <div
      style={{
        position: "absolute",
        inset: "104px 70px 74px",
        opacity: state.opacity,
        translate: `0 ${state.y}px`,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          marginBottom: 18,
        }}
      >
        <div>
          <div
            style={{
              color: colors.amber,
              fontFamily: mono,
              fontSize: 13,
              fontWeight: 700,
              letterSpacing: "0.16em",
              marginBottom: 8,
            }}
          >
            AGENT DIFF INCOMING / 00:00:01
          </div>
          <div
            style={{
              color: colors.text,
              fontFamily: sans,
              fontSize: 44,
              lineHeight: 1.04,
              fontWeight: 690,
              letterSpacing: "-0.035em",
            }}
          >
            {Math.round(state.count).toLocaleString("en-US")} changed lines.
            <br />
            <span style={{color: "#8b99a3"}}>Too fast to read.</span>
          </div>
        </div>
        <div
          style={{
            width: 270,
            borderLeft: `1px solid ${colors.border}`,
            paddingLeft: 18,
            color: colors.muted,
            fontFamily: mono,
            fontSize: 12,
            lineHeight: 1.65,
          }}
        >
          <div style={{color: colors.coral}}>▲ VELOCITY SPIKE</div>
          12 files changing in parallel
          <br />
          human attention: saturated
        </div>
      </div>

      <div
        style={{
          height: 350,
          border: `1px solid rgba(255,107,95,${0.22 + state.pressure * 0.22})`,
          borderRadius: 17,
          background:
            "linear-gradient(180deg, rgba(17,22,28,0.98), rgba(8,11,15,0.98))",
          boxShadow: `0 26px 80px rgba(0,0,0,0.42), 0 0 ${24 + state.pressure * 28}px rgba(255,107,95,0.08)`,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: 42,
            borderBottom: `1px solid ${colors.border}`,
            display: "flex",
            alignItems: "center",
            padding: "0 16px",
            gap: 7,
            color: colors.muted,
            fontFamily: mono,
            fontSize: 11,
          }}
        >
          <span style={{width: 8, height: 8, borderRadius: 9, background: colors.coral}} />
          <span style={{width: 8, height: 8, borderRadius: 9, background: colors.amber}} />
          <span style={{width: 8, height: 8, borderRadius: 9, background: colors.green}} />
          <span style={{marginLeft: 9}}>agent/output.diff</span>
          <span style={{marginLeft: "auto", color: colors.coral}}>+8,420 −3,580</span>
        </div>
        <div
          style={{
            padding: "16px 22px",
            translate: `0 ${-state.stream * 24}px`,
            fontFamily: mono,
            fontSize: 15,
            lineHeight: 1.85,
          }}
        >
          {diffLines.slice(0, visibleRows).map((line, index) => (
            <div
              key={`${line.text}-${index}`}
              style={{
                display: "grid",
                gridTemplateColumns: "32px 1fr 132px",
                color: line.color,
                opacity: gsap.utils.clamp(0.2, 1, state.stream * 9 - index),
              }}
            >
              <span>{line.prefix}</span>
              <span>{line.text}</span>
              <span style={{color: "#53636f", textAlign: "right"}}>
                {118 + index * 7}:{index % 3 === 0 ? "context" : "change"}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
