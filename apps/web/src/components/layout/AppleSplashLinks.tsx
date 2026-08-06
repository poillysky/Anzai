/**
 * iOS apple-touch-startup-image set (web.dev / Expo PWA splash guidance).
 * Exact pixel sizes required; media queries target CSS width/height × DPR.
 */
export function AppleSplashLinks() {
  const links: { href: string; media: string }[] = [
    {
      href: "/splash/iphone_x-1125x2436.png?v=2",
      media:
        "(device-width: 375px) and (device-height: 812px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)",
    },
    {
      href: "/splash/iphone14-1170x2532.png?v=2",
      media:
        "(device-width: 390px) and (device-height: 844px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)",
    },
    {
      href: "/splash/iphone15-1179x2556.png?v=2",
      media:
        "(device-width: 393px) and (device-height: 852px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)",
    },
    {
      href: "/splash/iphone12pro_max-1284x2778.png?v=2",
      media:
        "(device-width: 428px) and (device-height: 926px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)",
    },
    {
      href: "/splash/iphone15pro_max-1290x2796.png?v=2",
      media:
        "(device-width: 430px) and (device-height: 932px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)",
    },
    {
      href: "/splash/iphone_xr-828x1792.png?v=2",
      media:
        "(device-width: 414px) and (device-height: 896px) and (-webkit-device-pixel-ratio: 2) and (orientation: portrait)",
    },
    {
      href: "/splash/iphone11pro_max-1242x2688.png?v=2",
      media:
        "(device-width: 414px) and (device-height: 896px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)",
    },
  ];

  return (
    <>
      <link rel="apple-touch-icon" href="/icons/apple-touch-icon-180.png?v=2" />
      {links.map((l) => (
        <link
          key={l.media}
          rel="apple-touch-startup-image"
          href={l.href}
          media={l.media}
        />
      ))}
    </>
  );
}
