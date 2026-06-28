#!/bin/bash
# SRE Platform Frontend Build and Deploy Script

echo "1. Building frontend with production SRE API URL..."
VITE_API_URL=https://sre-api.trihonor.com npm run build

echo ""
echo "2. Ready to deploy to Cloudflare Pages!"
echo "Please run the following command on your machine with your Cloudflare API token:"
echo "CLOUDFLARE_API_TOKEN=your_api_token npx wrangler pages deploy dist --project-name=sre-platform"
