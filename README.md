# Immich Autoindex Sync for E-Ink Displays

This project syncs portrait-oriented images from [Immich](https://immich.app/) "On This Day" and prepares them for E-Ink Spectra 6 displays. It applies resizing, 180-degree rotation, and Floyd-Steinberg dithering using the native 6-color palette of the Spectra 6.

## Features

- **Immich Integration**: Automatically searches for portrait images taken on the current date across previous years.
- **Image Processing**: 
  - Resizes to 1200x1600 (portrait).
  - Letterboxes with white background if the aspect ratio differs significantly.
  - Rotates 180 degrees (configurable/hardcoded for specific display orientations).
  - Applies Floyd-Steinberg dithering to the native E-Ink Spectra 6 color palette.
- **Multiple Formats**: Generates both standard `.jpg` and bitplane-optimized `.spectra6` files.
- **HTTP Server**: Built-in server to serve a random processed image per request, with intelligent redirection to the preferred format.
- **Docker Support**: Easy deployment with Docker and Docker Compose.

## Prerequisites

- An Immich instance with an API key.
- The [thepiwo/PhotoPainter-E-Ink-Spectra-6-image-converter](https://github.com/thepiwo/PhotoPainter-E-Ink-Spectra-6-image-converter) repository (included as a submodule or sibling in the Docker build). This fork is used for its optimized E-Ink Spectra 6 bitplane output and integration-ready dithering logic.

## Configuration

The service is configured via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `IMMICH_API_URL` | Base URL of your Immich API | **Required** |
| `IMMICH_API_KEY` | Your Immich API key | **Required** |
| `YEARS_BACK` | How many years to look back for "On This Day" | `5` |
| `DITHER` | Whether to apply E-Ink dithering | `true` |
| `DITHER_FORMAT` | Output format for dithered images (`spectra6` or `bmp`) | `spectra6` |
| `IMAGE_DIR` | Where to store processed images | `/data/images` |
| `PORT` | HTTP server port | `8080` |
| `SOURCE_REPO_PATH` | Path to the PhotoPainter converter repository | `../PhotoPainter...` |

## Usage with Docker Compose

```yaml
services:
  sync:
    image: immich-autoindex-sync:latest
    environment:
      - IMMICH_API_URL=https://your-immich.com/api
      - IMMICH_API_KEY=your-api-key
      - YEARS_BACK=10
    volumes:
      - ./images:/data/images
    restart: unless-stopped
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
