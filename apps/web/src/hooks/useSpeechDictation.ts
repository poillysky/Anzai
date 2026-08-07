"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type SpeechRec = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((ev: SpeechRecEvent) => void) | null;
  onerror: ((ev: { error?: string }) => void) | null;
  onend: (() => void) | null;
};

type SpeechRecEvent = {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
};

type SpeechRecCtor = new () => SpeechRec;

function getSpeechRecognitionCtor(): SpeechRecCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as Window & {
    SpeechRecognition?: SpeechRecCtor;
    webkitSpeechRecognition?: SpeechRecCtor;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function speechDictationSupported(): boolean {
  return getSpeechRecognitionCtor() != null;
}

type Options = {
  lang?: string;
  /** Called with cumulative transcript for this listening session (interim + final). */
  onTranscript: (text: string, isFinal: boolean) => void;
  onUnsupported?: () => void;
  onError?: (message: string) => void;
};

/**
 * Mobile-friendly dictation via Web Speech API (Chrome Android / desktop).
 * Chrome sends audio to Google speech servers — mainland CN often fails with
 * network / TLS unless system proxy can reach Google.
 * iOS Safari / home-screen PWA usually unsupported → onUnsupported.
 */
export function useSpeechDictation({
  lang = "zh-CN",
  onTranscript,
  onUnsupported,
  onError,
}: Options) {
  const [listening, setListening] = useState(false);
  const [supported, setSupported] = useState(false);
  const recRef = useRef<SpeechRec | null>(null);
  const wantRef = useRef(false);
  const onTranscriptRef = useRef(onTranscript);
  const onErrorRef = useRef(onError);
  onTranscriptRef.current = onTranscript;
  onErrorRef.current = onError;

  useEffect(() => {
    setSupported(speechDictationSupported());
  }, []);

  const stop = useCallback(() => {
    wantRef.current = false;
    const rec = recRef.current;
    recRef.current = null;
    if (rec) {
      // Detach first so late onresult/onend cannot refill the input after send.
      rec.onresult = null;
      rec.onerror = null;
      rec.onend = null;
      try {
        rec.abort();
      } catch {
        try {
          rec.stop();
        } catch {
          /* already stopped */
        }
      }
    }
    setListening(false);
  }, []);

  const start = useCallback(() => {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      onUnsupported?.();
      return;
    }
    if (typeof window !== "undefined" && !window.isSecureContext) {
      onErrorRef.current?.(
        "当前是 HTTP 访问，浏览器不开放麦克风。请用 https 打开，或用系统键盘上的麦克风",
      );
      return;
    }
    stop();
    wantRef.current = true;
    const rec = new Ctor();
    rec.lang = lang;
    // One utterance per start — fewer Google reconnects (TLS) on CN networks
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    let committed = "";

    rec.onresult = (ev) => {
      if (!wantRef.current) return;
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const row = ev.results[i];
        const piece = (row[0]?.transcript || "").trim();
        if (!piece) continue;
        if (row.isFinal) {
          committed = committed ? `${committed}${piece}` : piece;
        } else {
          interim = interim ? `${interim}${piece}` : piece;
        }
      }
      const out = interim ? (committed ? `${committed}${interim}` : interim) : committed;
      if (!wantRef.current) return;
      onTranscriptRef.current(out, !interim && Boolean(committed));
    };

    rec.onerror = (ev) => {
      const code = (ev.error || "").toLowerCase();
      if (code === "aborted" || code === "no-speech") return;
      if (code === "not-allowed") {
        onErrorRef.current?.("麦克风权限被拒绝，请在系统设置里允许");
      } else if (code === "network" || code === "service-not-allowed") {
        onErrorRef.current?.(
          "语音识别连不上 Google（常见 TLS/网络限制）。可开系统代理后再试，或长按键盘麦克风输入",
        );
      } else if (code) {
        onErrorRef.current?.(`语音识别失败（${code}）`);
      }
      wantRef.current = false;
      setListening(false);
    };

    rec.onend = () => {
      if (wantRef.current) {
        try {
          rec.start();
          return;
        } catch {
          wantRef.current = false;
        }
      }
      setListening(false);
      recRef.current = null;
    };

    recRef.current = rec;
    try {
      rec.start();
      setListening(true);
    } catch {
      wantRef.current = false;
      setListening(false);
      onErrorRef.current?.("无法启动语音识别");
    }
  }, [lang, onUnsupported, stop]);

  const toggle = useCallback(() => {
    if (listening) stop();
    else start();
  }, [listening, start, stop]);

  useEffect(() => () => stop(), [stop]);

  return { supported, listening, start, stop, toggle };
}
