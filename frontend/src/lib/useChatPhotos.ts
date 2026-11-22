import { useEffect, useRef, useState } from "react";

export type ChatPhoto = { id: string; url: string; name: string };

const MAX_PHOTOS = 3;
const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const PHOTO_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);

function photoForSearch(image: HTMLImageElement): string {
  const scale = Math.min(1, 512 / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("사진을 처리할 수 없어요.");
  context.fillStyle = "white";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.8);
}

// PRD 4.2 — 검색에 보낼 축소 사진을 브라우저 메모리에서만 보관한다.
export function useChatPhotos() {
  const [photos, setPhotos] = useState<ChatPhoto[]>([]);
  const [photoError, setPhotoError] = useState("");
  const [addingPhotos, setAddingPhotos] = useState(false);
  const pending = useRef<ChatPhoto[]>([]);
  const loading = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  async function addPhotos(files: File[]) {
    if (loading.current) return;
    const available = MAX_PHOTOS - pending.current.length;
    if (available <= 0) {
      setPhotoError("사진은 한 번에 최대 3장까지 첨부할 수 있어요.");
      return;
    }
    loading.current = true;
    setAddingPhotos(true);
    setPhotoError("");
    const issues: string[] = [];
    if (files.length > available) issues.push("사진은 한 번에 최대 3장까지 첨부할 수 있어요.");
    try {
      for (const file of files.slice(0, available)) {
        if (!PHOTO_TYPES.has(file.type)) {
          issues.push("JPG, PNG, WebP, GIF 사진을 첨부해 주세요.");
          continue;
        }
        if (file.size > MAX_PHOTO_BYTES) {
          issues.push("사진 한 장은 10MB 이하여야 해요.");
          continue;
        }
        const url = URL.createObjectURL(file);
        try {
          const image = new Image();
          image.src = url;
          await image.decode();
          if (!mounted.current) return;
          pending.current = [...pending.current, { id: crypto.randomUUID(), url: photoForSearch(image), name: file.name || "붙여넣은 사진" }];
          setPhotos(pending.current);
        } catch {
          issues.push("사진을 열지 못했어요. 다른 사진을 선택해 주세요.");
        } finally {
          URL.revokeObjectURL(url);
        }
      }
    } finally {
      loading.current = false;
      if (mounted.current) {
        setAddingPhotos(false);
        setPhotoError([...new Set(issues)].join(" "));
      }
    }
  }

  function removePhoto(id: string) {
    pending.current = pending.current.filter(photo => photo.id !== id);
    setPhotos(pending.current);
    setPhotoError("");
  }

  function takePhotos() {
    const selected = pending.current;
    pending.current = [];
    setPhotos([]);
    setPhotoError("");
    return selected;
  }

  return { photos, photoError, addingPhotos, addPhotos, removePhoto, takePhotos };
}
