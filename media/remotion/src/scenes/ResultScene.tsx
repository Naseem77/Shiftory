import React from "react";
import {useCurrentFrame, useVideoConfig} from "remotion";
import {colors, mono, sans} from "../constants";
import {useGsapState} from "../gsap-state";

const badges = [
  {label: "100% ACCOUNTED", color: colors.green, icon: "✓"},
  {label: "SOURCE-CITED", color: colors.cyan, icon: "⌁"},
  {label: "EXPLAINS, NEVER REVIEWS", color: colors.amber, icon: "◇"},
];

const facts = [
  {
    title: "Actor context now survives the queue",
    body: "Shift assignment carries the initiating actor through enqueue and dispatch.",
    cite: "scheduler.py:118–164",
  },
  {
    title: "Routing is selected from live context",
    body: "The scheduler replaces first-available selection with context-aware assignment.",
    cite: "queue.py:72–109",
  },
  {
    title: "Every changed unit has an owner",
    body: "12,000 lines, 184 hunks, and 37 non-text units resolve into cited evidence.",
    cite: "evidence.json#owners",
  },
];

export const ResultScene: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const state = useGsapState(
    frame,
    fps,
    () => ({opacity: 0, y: 30, facts: 0, badges: 0, rule: 0}),
    (timeline, value) => {
      timeline
        .to(value, {opacity: 1, y: 0, duration: 0.62, ease: "power3.out"}, 4.72)
        .to(value, {rule: 1, duration: 0.75, ease: "power2.inOut"}, 4.9)
        .to(value, {facts: 3, duration: 1.05, ease: "power3.out"}, 5.05)
        .to(value, {badges: 3, duration: 0.8, ease: "power3.out"}, 5.72);
    },
  );

  return (
    <div
      style={{
        position: "absolute",
        inset: "104px 70px 75px",
        opacity: state.opacity,
        translate: `0 ${state.y}px`,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          marginBottom: 17,
        }}
      >
        <div>
          <div
            style={{
              color: colors.cyan,
              fontFamily: mono,
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: "0.15em",
              marginBottom: 8,
            }}
          >
            VERIFIED CHANGE STORY
          </div>
          <div
            style={{
              color: colors.text,
              fontFamily: sans,
              fontSize: 43,
              fontWeight: 700,
              letterSpacing: "-0.035em",
            }}
          >
            12,000 lines <span style={{color: colors.cyan}}>→</span> 3 human facts.
          </div>
        </div>
        <div
          style={{
            color: colors.muted,
            fontFamily: mono,
            fontSize: 12,
            lineHeight: 1.55,
            textAlign: "right",
          }}
        >
          exact changed-line / hunk / unit accounting
          <br />
          <span style={{color: colors.green}}>verification passed</span>
        </div>
      </div>

      <div
        style={{
          height: 337,
          border: `1px solid ${colors.border}`,
          borderRadius: 18,
          background:
            "linear-gradient(135deg, rgba(15,21,27,0.99), rgba(8,12,16,0.99))",
          boxShadow: "0 30px 90px rgba(0,0,0,0.45)",
          padding: "20px 24px",
        }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            height: 233,
          }}
        >
          {facts.map((fact, index) => {
            const reveal = Math.max(0, Math.min(1, state.facts - index));
            return (
              <article
                key={fact.title}
                style={{
                  position: "relative",
                  padding: "17px 22px 15px",
                  borderRight: index < facts.length - 1 ? `1px solid ${colors.border}` : "none",
                  opacity: reveal,
                  translate: `0 ${(1 - reveal) * 16}px`,
                }}
              >
                <div
                  style={{
                    color: colors.cyan,
                    fontFamily: mono,
                    fontSize: 11,
                    letterSpacing: "0.14em",
                    marginBottom: 13,
                  }}
                >
                  0{index + 1} / CHANGE
                </div>
                <h2
                  style={{
                    color: colors.text,
                    fontFamily: sans,
                    fontSize: 21,
                    lineHeight: 1.22,
                    fontWeight: 680,
                    letterSpacing: "-0.018em",
                    margin: 0,
                  }}
                >
                  {fact.title}
                </h2>
                <p
                  style={{
                    color: "#98a8b2",
                    fontFamily: sans,
                    fontSize: 14,
                    lineHeight: 1.48,
                    margin: "12px 0 0",
                  }}
                >
                  {fact.body}
                </p>
                <div
                  style={{
                    position: "absolute",
                    left: 22,
                    bottom: 15,
                    color: colors.amber,
                    fontFamily: mono,
                    fontSize: 10,
                    backgroundColor: "rgba(255,191,105,0.06)",
                    border: "1px solid rgba(255,191,105,0.18)",
                    borderRadius: 5,
                    padding: "6px 8px",
                  }}
                >
                  [{fact.cite}]
                </div>
              </article>
            );
          })}
        </div>
        <div
          style={{
            width: `${state.rule * 100}%`,
            height: 1,
            background: `linear-gradient(90deg, transparent, ${colors.cyan}, transparent)`,
            opacity: 0.42,
          }}
        />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            height: 83,
          }}
        >
          {badges.map((badge, index) => {
            const reveal = Math.max(0, Math.min(1, state.badges - index));
            return (
              <div
                key={badge.label}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 9,
                  padding: "11px 15px",
                  border: `1px solid ${badge.color}44`,
                  borderRadius: 999,
                  backgroundColor: `${badge.color}0d`,
                  color: badge.color,
                  fontFamily: mono,
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: "0.08em",
                  opacity: reveal,
                  scale: 0.9 + reveal * 0.1,
                }}
              >
                <span style={{fontSize: 15}}>{badge.icon}</span>
                {badge.label}
              </div>
            );
          })}
        </div>
      </div>
      <div
        style={{
          marginTop: 15,
          textAlign: "center",
          color: colors.text,
          fontFamily: mono,
          fontSize: 15,
        }}
      >
        <span style={{color: colors.cyan}}>❯</span>&nbsp; shiftory explain
        <span style={{color: "#596872"}}>&nbsp;— evidence before eloquence.</span>
      </div>
    </div>
  );
};
