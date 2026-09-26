import { PREVIEW_THEMES, type PreviewTheme } from "../lib/theme";

interface ThemePreviewProps {
  theme: PreviewTheme;
  onChange: (theme: PreviewTheme) => void;
}

export function ThemePreview({ theme, onChange }: ThemePreviewProps) {
  if (!import.meta.env.DEV) return null;

  return (
    <aside className="theme-preview" aria-label="개발용 테마 미리보기">
      <span className="theme-preview-label">THEME</span>
      {PREVIEW_THEMES.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={theme === option.value}
          title={option.description}
          onClick={() => onChange(option.value)}
        >
          {option.shortLabel}
        </button>
      ))}
    </aside>
  );
}
