/** @type {import('next').NextConfig} */
const nextConfig = {
  // Lint runs in CI via npx eslint; builds stay deterministic without the binary.
  eslint: { ignoreDuringBuilds: true },
};
export default nextConfig;
