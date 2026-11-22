import { useEffect, useRef, useState } from "react";

export type ChatPhoto = { id: string; url: string; name: string };

const MAX_PHOTOS = 3;
const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const PHOTO_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);

// PRD 4.2 — 첨부 UI만 제공한다. 파일은 업로드·영구 저장하지 않고 현재 화면에서만 보관한다.
export function useChatPhotos() {
  const [photos, setPhotos] = useState<ChatPhoto[]>([]);
  const [photoError, setPhotoError] = useState("");
  const [addingPhotos, setAddingPhotos] = useState(false);
  const pending = useRef<ChatPhoto[]>([]);
  const objectUrls = useRef(new Set<string>());
  const chatUrls = useRef(new Set<string>());
  const loading = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      objectUrls.current.forEach(URL.revokeObjectURL);
      objectUrls.current.clear();
      chatUrls.current.clear();
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
        objectUrls.current.add(url);
        try {
          const image = new Image();
          image.src = url;
          await image.decode();
          if (!mounted.current) return;
          pending.current = [...pending.current, { id: crypto.randomUUID(), url, name: file.name || "붙여넣은 사진" }];
          setPhotos(pending.current);
        } catch {
          URL.revokeObjectURL(url);
          objectUrls.current.delete(url);
          issues.push("사진을 열지 못했어요. 다른 사진을 선택해 주세요.");
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
    const photo = pending.current.find(photo => photo.id === id);
    if (photo && !chatUrls.current.has(photo.url)) {
      URL.revokeObjectURL(photo.url);
      objectUrls.current.delete(photo.url);
    }
    pending.current = pending.current.filter(photo => photo.id !== id);
    setPhotos(pending.current);
    setPhotoError("");
  }

  function takePhotos(ids: string[]) {
    const selected = pending.current.filter(photo => ids.includes(photo.id));
    pending.current = pending.current.filter(photo => !ids.includes(photo.id));
    setPhotos(pending.current);
    setPhotoError("");
    // 전송한 말풍선에서도 URL을 사용하므로 대화 화면을 떠날 때 해제한다.
    return selected;
  }

  // 전송 실패 후 초안을 삭제해도 이미 보낸 말풍선의 사진은 남긴다.
  function retainPhotosInChat(selected: ChatPhoto[]) {
    selected.forEach(photo => chatUrls.current.add(photo.url));
  }

  function restorePhotos(selected: ChatPhoto[]) {
    const existing = new Set(pending.current.map(photo => photo.id));
    pending.current = [...pending.current, ...selected.filter(photo => !existing.has(photo.id))];
    setPhotos(pending.current);
  }

  return { photos, photoError, addingPhotos, addPhotos, removePhoto, takePhotos, retainPhotosInChat, restorePhotos };
}
