/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // TypeScript errors will now fail the build - this is safer
  typescript: {
    // Set to false to catch type errors during build
    ignoreBuildErrors: false,
  },
  // ESLint errors will now fail the build - this is safer
  eslint: {
    // Set to false to catch linting errors during build
    ignoreDuringBuilds: false,
  },
}

module.exports = nextConfig

