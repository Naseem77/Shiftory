import {useMemo} from "react";
import {gsap} from "gsap";

export const useGsapState = <T extends object>(
  frame: number,
  fps: number,
  initialState: () => T,
  buildTimeline: (timeline: gsap.core.Timeline, state: T) => void,
): T => {
  const state = useMemo(initialState, []);
  const timeline = useMemo(() => {
    const nextTimeline = gsap.timeline({paused: true});
    buildTimeline(nextTimeline, state);
    return nextTimeline;
  }, [state]);

  timeline.seek(frame / fps, false);
  return state;
};
