export interface MapColorTokens {
  clusterFills: string[];
  clusterBorder: string;
  clusterShadow: string;
  textOnSolid: string;
  pinPartner: string;
  pinPolice: string;
  pinBadge: string;
  pinShadow: string;
  pinPartnerShadow: string;
  pinPoliceShadow: string;
  radiusStroke: string;
  radiusFill: string;
}

export const PREVIEW_THEMES = [
  {
    value: "warm-ivory",
    shortLabel: "Warm",
    description: "밝은 아이보리와 부드러운 갈색",
  },
  {
    value: "deep-cocoa",
    shortLabel: "Cocoa",
    description: "짙은 갈색과 절제된 크림색",
  },
  {
    value: "retriever-modern",
    shortLabel: "Modern",
    description: "아이보리·진한 갈색에 코랄을 더한 균형형",
  },
] as const;

export type PreviewTheme = (typeof PREVIEW_THEMES)[number]["value"];

const PREVIEW_THEME_STORAGE_KEY = "retriever-preview-theme";

export function readPreviewTheme(): PreviewTheme {
  const storedTheme = window.localStorage.getItem(PREVIEW_THEME_STORAGE_KEY);
  const match = PREVIEW_THEMES.find((theme) => theme.value === storedTheme);
  return match?.value || "retriever-modern";
}

export function applyPreviewTheme(theme: PreviewTheme) {
  document.documentElement.dataset.theme = theme;
  window.localStorage.setItem(PREVIEW_THEME_STORAGE_KEY, theme);
}

function cssColor(name: string) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!value) throw new Error(`필수 색상 토큰이 없습니다: ${name}`);
  return value;
}

export function readMapColorTokens(): MapColorTokens {
  return {
    clusterFills: [
      cssColor("--color-map-cluster-1"),
      cssColor("--color-map-cluster-2"),
      cssColor("--color-map-cluster-3"),
      cssColor("--color-map-cluster-4"),
      cssColor("--color-map-cluster-5"),
    ],
    clusterBorder: cssColor("--color-map-cluster-border"),
    clusterShadow: cssColor("--color-map-cluster-shadow"),
    textOnSolid: cssColor("--color-text-on-solid"),
    pinPartner: cssColor("--color-source-partner"),
    pinPolice: cssColor("--color-source-police"),
    pinBadge: cssColor("--color-map-pin-badge"),
    pinShadow: cssColor("--color-text-primary"),
    pinPartnerShadow: cssColor("--color-map-pin-partner-shadow"),
    pinPoliceShadow: cssColor("--color-map-pin-police-shadow"),
    radiusStroke: cssColor("--color-map-radius-stroke"),
    radiusFill: cssColor("--color-map-radius-fill"),
  };
}
