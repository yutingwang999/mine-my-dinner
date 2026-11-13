import { ApiError } from "@/lib/api";

/**
 * Copies picked files into memory. A File from an input is only a reference to
 * the file on disk, read at upload time, so moving or editing the original
 * before the draft is saved would make it unreadable.
 */
export async function copyImages(files: File[]): Promise<File[]> {
  try {
    return await Promise.all(
      files.map(
        async (file) => new File([await file.arrayBuffer()], file.name, { type: file.type }),
      ),
    );
  } catch {
    throw new ApiError(0, "images_unreadable");
  }
}
