# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Full B.LEEGUE men's basketball stream aggregation service
- JSON API endpoints (`/games/live`, `/games/completed`) with game phase computation, team name resolution, and stream URL resolution
- M3U playlist endpoint (`/pl/`) with grouped channels (Live/Catch-Up/Upcoming/Completed) and IPTV-compatible metadata
- XMLTV EPG endpoint (`/pl/epg.xml`) with programme slots, Japanese team names, venue info, round context, and JST→UTC conversion
- AES-12 license key proxy (`/playkey`) injecting `X-BASKETLIVE-FREEKEY` credential on upstream request
- CDN forwarder (`/{path:path}`) rewriting `#EXT-X-KEY` URIs from softbank playkey → local proxy for player decryption
- Stream redirect endpoint (`/stream/{match_id}`) resolving game-detail URLs and returning 302 redirects

### Changed
- Forked from nbl1-streams; adapted all B.LEEGUE-specific logic including schedule format, team catalog structure, phase computation rules, encrypted HLS model, and quality presets

### Notes
- All credentials hardcoded in source (matching nbl1 pattern); no environment variables required
- Service deploys to Google Cloud Run `asia-northeast1` (Japan) — mandatory for geo-unblocked API access
- Game phase computed via clock-based evaluation per decompiled Android app reverse-engineering
