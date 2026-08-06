"use client";

import { BatteryFull, ICON_SIZE_STATUS, Signal, Wifi } from "@/components/ui/icons";
import { useEffect, useState } from "react";

/** Desktop phone-frame status bar preview — Lucide icons only. */
export function StatusBar() {
  const [time, setTime] = useState("9:41");

  useEffect(() => {
    const tick = () => {
      const d = new Date();
      setTime(`${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`);
    };
    tick();
    const id = setInterval(tick, 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="ios-status-bar" aria-hidden>
      <span className="ios-status-time">{time}</span>
      <div className="ios-status-right">
        <Signal className="ios-status-icon" size={ICON_SIZE_STATUS} strokeWidth={2} absoluteStrokeWidth />
        <Wifi className="ios-status-icon" size={ICON_SIZE_STATUS} strokeWidth={2} absoluteStrokeWidth />
        <BatteryFull className="ios-status-icon" size={ICON_SIZE_STATUS + 2} strokeWidth={2} absoluteStrokeWidth />
      </div>
    </div>
  );
}
