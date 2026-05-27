export function GET() {
  return Response.json({
    ok: true,
    runtime: "next",
    revision: process.env.IMAGE_REVISION ?? "unknown"
  });
}
