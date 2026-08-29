import React from "react";
import {colors, mono, sans} from "../constants";

export const Chrome: React.FC = () => {
  return (
    <>
      <div
        style={{
          position: "absolute",
          top: 40,
          left: 56,
          right: 56,
          height: 48,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{display: "flex", alignItems: "center", gap: 13}}>
          <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
            <path
              d="M5 8.5 15 3l10 5.5v13L15 27 5 21.5Z"
              fill="none"
              stroke={colors.cyan}
              strokeWidth="1.7"
            />
            <circle cx="15" cy="8" r="2.4" fill={colors.cyan} />
            <circle cx="10" cy="19" r="2.4" fill={colors.amber} />
            <circle cx="21" cy="18" r="2.4" fill={colors.green} />
            <path d="m15 10-4.2 7M16.8 9.6l3.3 6.2M12.4 19h6.2" stroke="#71828e" />
          </svg>
          <div>
            <div
              style={{
                color: colors.text,
                fontFamily: sans,
                fontWeight: 760,
                fontSize: 21,
                letterSpacing: "0.12em",
              }}
            >
              SHIFTORY
            </div>
            <div
              style={{
                color: colors.muted,
                fontFamily: mono,
                fontSize: 10,
                letterSpacing: "0.18em",
                marginTop: 2,
              }}
            >
              CHANGE INTELLIGENCE
            </div>
          </div>
        </div>
        <div
          style={{
            color: colors.muted,
            fontFamily: mono,
            fontSize: 11,
            letterSpacing: "0.11em",
            border: `1px solid ${colors.border}`,
            borderRadius: 999,
            padding: "9px 14px",
            backgroundColor: "rgba(8,12,16,0.7)",
          }}
        >
          LOCAL-FIRST&nbsp;&nbsp;•&nbsp;&nbsp;GIT-AUTHORITATIVE
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          left: 56,
          right: 56,
          bottom: 33,
          display: "flex",
          alignItems: "center",
          gap: 14,
          color: "#62727d",
          fontFamily: mono,
          fontSize: 11,
          letterSpacing: "0.08em",
        }}
      >
        <span style={{color: colors.cyan}}>●</span>
        <span>DETERMINISTIC EVIDENCE</span>
        <span style={{height: 1, flex: 1, backgroundColor: "#1e2931"}} />
        <span>EXPLAIN CHANGES. NEVER REVIEW THEM.</span>
      </div>
    </>
  );
};
