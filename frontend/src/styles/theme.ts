import { createDarkTheme, createLightTheme, type BrandVariants, type Theme } from "@fluentui/react-components";

/**
 * Executive-grade brand ramp modeled on Azure blue - deliberately not the
 * default Fluent "brand" (teams purple) so Genie reads as a distinct,
 * Microsoft-inspired but not Teams-branded product.
 */
const genieBrandRamp: BrandVariants = {
  10: "#040509",
  20: "#0a1424",
  30: "#0d1f3c",
  40: "#0f2a52",
  50: "#123566",
  60: "#14417c",
  70: "#154d93",
  80: "#1659aa",
  90: "#1666c1",
  100: "#1373d9",
  110: "#2f83e0",
  120: "#4e93e5",
  130: "#6ba3ea",
  140: "#87b3ef",
  150: "#a3c4f3",
  160: "#bfd5f7",
};

export const genieLightTheme: Theme = createLightTheme(genieBrandRamp);
export const genieDarkTheme: Theme = createDarkTheme(genieBrandRamp);

/**
 * Semantic status colors for governance/risk/confidence indicators used
 * across the Requirements, Architecture, and Governance pages. These are
 * deliberately restrained (no neon/arcade tones) per the executive-grade
 * design requirement.
 */
export const statusPalette = {
  compliant: "#3fa66a",
  warning: "#d8a325",
  blocked: "#c94f4f",
  incomplete: "#8a8f98",
  failed: "#b0362f",
  info: "#2f83e0",
} as const;
