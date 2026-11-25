export function errorText(error: unknown): string {
  if (error && typeof error === "object" && "body" in error) {
    const body = error.body;
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
      return body.detail;
    }
  }
  return error instanceof Error ? error.message : "Request failed";
}