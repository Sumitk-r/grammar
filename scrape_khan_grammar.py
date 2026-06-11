#!/usr/bin/env python3
"""Scrape Khan Academy Grammar video transcripts into CSV/text files.

This script uses Khan Academy's current internal GraphQL content route queries.
The old /api/v1 endpoints have been removed, and normal HTML requests may be
served a client challenge, so using the safelisted GraphQL operations is more
reliable than scraping rendered pages.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


KHAN_BASE_URL = "https://www.khanacademy.org"


CONTENT_ROUTE_COURSE_DATA_QUERY = r"""query ContentRouteCourseData($path: String!, $countryCode: String!) {
  content {
    metadata {
      commitSha
      __typename
    }
    __typename
  }
  contentRoute(path: $path, countryCode: $countryCode) {
    resolvedPath
    listedPathData {
      course {
        ...CourseData
        unitChildren {
          ...UnitData
          allOrderedChildren {
            ... on Lesson {
              ...LessonData
              __typename
            }
            ... on TopicQuiz {
              ...QuizMetadata
              __typename
            }
            ... on TopicUnitTest {
              ...UnitTestMetadata
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    unlistedPathData {
      course {
        ...CourseData
        unitChildren {
          ...UnitData
          allOrderedChildren {
            ... on Lesson {
              ...LessonData
              __typename
            }
            ... on TopicQuiz {
              ...QuizMetadata
              __typename
            }
            ... on TopicUnitTest {
              ...UnitTestMetadata
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}

fragment CourseData on Course {
  id
  iconPath
  masteryEnabled
  relativeUrl
  slug
  translatedTitle
  translatedDescription
  isListedForLearners
  translatedCustomTitleTag
  contentKind
  userAuthoredContentTypes
  masterableExercises(includeDuplicates: true) {
    id
    __typename
  }
  parent {
    id
    contentKind
    relativeUrl
    slug
    translatedTitle
    __typename
  }
  lowerToc
  curation {
    hideSubjectIntro
    hideCommunityQuestions
    sponsorFooterAttribution {
      footnoteHtml
      imageBaselineAligned
      imageCaption
      imageUrl
      taglineHtml
      __typename
    }
    modules {
      kind
      untranslatedFields
      ... on CourseIntroModule {
        callToAction
        description
        link
        title
        video
        __typename
      }
      ... on ActionListModule {
        actions {
          text
          URL: url
          contentDescriptor
          __typename
        }
        kind
        title
        __typename
      }
      ... on PartnershipDescriptionModule {
        description
        imageCaption
        imageUrl
        isOutro
        partnerUrl
        partnerUrlText
        __typename
      }
      ... on ContentCarouselModule {
        referrer
        title
        contentDescriptors
        __typename
      }
      __typename
    }
    excludedChildren
    __typename
  }
  courseChallenge {
    id
    contentKind
    slug
    contentDescriptor
    parentTopic {
      id
      parent {
        id
        masteryEnabled
        __typename
      }
      __typename
    }
    urlWithinCurationNode
    exerciseLength
    timeEstimate {
      lowerBound
      upperBound
      __typename
    }
    __typename
  }
  masteryChallenge {
    id
    contentKind
    slug
    contentDescriptor
    parentTopic {
      id
      parent {
        id
        masteryEnabled
        __typename
      }
      __typename
    }
    urlWithinCurationNode
    exerciseLength
    timeEstimate {
      lowerBound
      upperBound
      __typename
    }
    __typename
  }
  __typename
}

fragment LearnableContentMetadata on LearnableContent {
  id
  canonicalUrl: defaultUrlPath
  contentDescriptor
  contentKind
  parentTopic {
    id
    parent {
      id
      masteryEnabled
      __typename
    }
    __typename
  }
  progressKey
  slug
  translatedCustomTitleTag
  translatedDescription
  translatedTitle
  urlWithinCurationNode
  ... on Challenge {
    userAuthoredContentType
    __typename
  }
  ... on Interactive {
    userAuthoredContentType
    __typename
  }
  ... on Project {
    userAuthoredContentType
    __typename
  }
  __typename
}

fragment LessonData on Lesson {
  id
  relativeUrl
  slug
  translatedDescription
  translatedTitle
  key
  curatedChildren(includeUnlisted: false) {
    ... on LearnableContent {
      ...LearnableContentMetadata
      __typename
    }
    ... on Exercise {
      exerciseLength
      isSkillCheck
      sponsored
      thumbnailUrl
      timeEstimate {
        lowerBound
        upperBound
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}

fragment QuizMetadata on TopicQuiz {
  ...LearnableContentMetadata
  exerciseLength
  index
  timeEstimate {
    lowerBound
    upperBound
    __typename
  }
  __typename
}

fragment UnitData on Unit {
  id
  iconPath
  masteryEnabled
  relativeUrl
  slug
  isListedForLearners
  translatedCustomTitleTag
  translatedDescription
  translatedTitle
  unlistedAncestorIds
  __typename
}

fragment UnitTestMetadata on TopicUnitTest {
  ...LearnableContentMetadata
  exerciseLength
  timeEstimate {
    lowerBound
    upperBound
    __typename
  }
  __typename
}"""


CONTENT_ROUTE_LESSON_AND_CONTENT_DATA_QUERY = r"""query ContentRouteLessonAndContentData($path: String!, $countryCode: String!) {
  contentRoute(path: $path, countryCode: $countryCode) {
    resolvedPath
    listedPathData {
      lesson {
        ...LessonData
        __typename
      }
      content {
        ...LearnableContentData
        __typename
      }
      __typename
    }
    unlistedPathData {
      lesson {
        ...LessonData
        __typename
      }
      content {
        ...LearnableContentData
        __typename
      }
      __typename
    }
    __typename
  }
}

fragment LearnableContentData on LearnableContent {
  id
  contentKind
  slug
  translatedTitle
  ... on Article {
    articleClarificationsEnabled: clarificationsEnabled
    translatedDescription
    translatedPerseusContent
    __typename
  }
  ... on Challenge {
    authorList {
      name
      __typename
    }
    canvasOnly
    code
    codeFormat
    configVersion
    defaultUrlPath
    height
    nodeSlug
    translatedDescription
    translatedTests
    testsFormat
    testStrings {
      message
      __typename
    }
    userAuthoredContentType
    width
    __typename
  }
  ... on Exercise {
    problemTypeKind
    __typename
  }
  ... on Interactive {
    authorList {
      name
      __typename
    }
    canvasOnly
    code
    codeFormat
    configVersion
    defaultUrlPath
    height
    nodeSlug
    translatedDescription
    userAuthoredContentType
    width
    __typename
  }
  ... on Project {
    authorList {
      name
      __typename
    }
    canvasOnly
    code
    codeFormat
    configVersion
    defaultUrlPath
    height
    nodeSlug
    translatedDescription
    translatedProjectEval
    translatedProjectEvalTips
    userAuthoredContentType
    width
    __typename
  }
  ... on Talkthrough {
    authorList {
      name
      __typename
    }
    canvasOnly
    code
    configVersion
    defaultUrlPath
    height
    nodeSlug
    playback
    subtitles {
      endTime
      kaIsValid
      startTime
      text
      __typename
    }
    translatedDescription
    translatedMp3Url
    userAuthoredContentType
    width
    youtubeId
    __typename
  }
  ... on TopicQuiz {
    index
    exerciseLength
    timeEstimate {
      lowerBound
      upperBound
      __typename
    }
    coveredTutorials {
      id
      translatedTitle
      relativeUrl
      allLearnableContent {
        id
        contentKind
        __typename
      }
      __typename
    }
    __typename
  }
  ... on TopicUnitTest {
    exerciseLength
    timeEstimate {
      lowerBound
      upperBound
      __typename
    }
    coveredTutorials {
      id
      translatedTitle
      relativeUrl
      allLearnableContent {
        id
        contentKind
        __typename
      }
      __typename
    }
    __typename
  }
  ... on Video {
    authorNames
    videoAuthorList: authorList {
      name
      __typename
    }
    clarificationsEnabled
    dateAdded
    description
    downloadUrls
    duration
    imageUrl
    kaUrl
    kaUserLicense
    keywords
    readableId
    sha
    thumbnailUrls {
      category
      url
      __typename
    }
    translatedDescriptionHtml
    translatedYoutubeId
    translatedYoutubeLang
    youtubeId
    augmentedTranscript
    relativeUrl
    descriptionHtml
    nodeSlug
    translatedDescription
    translatedCustomTitleTag
    subtitles {
      endTime
      kaIsValid
      startTime
      text
      __typename
    }
    keyMoments {
      startOffset
      endOffset
      label
      __typename
    }
    educationalLevel
    learningResourceType
    __typename
  }
  __typename
}

fragment LearnableContentMetadata on LearnableContent {
  id
  canonicalUrl: defaultUrlPath
  contentDescriptor
  contentKind
  parentTopic {
    id
    parent {
      id
      masteryEnabled
      __typename
    }
    __typename
  }
  progressKey
  slug
  translatedCustomTitleTag
  translatedDescription
  translatedTitle
  urlWithinCurationNode
  ... on Challenge {
    userAuthoredContentType
    __typename
  }
  ... on Interactive {
    userAuthoredContentType
    __typename
  }
  ... on Project {
    userAuthoredContentType
    __typename
  }
  __typename
}

fragment LessonData on Lesson {
  id
  relativeUrl
  slug
  translatedDescription
  translatedTitle
  key
  curatedChildren(includeUnlisted: false) {
    ... on LearnableContent {
      ...LearnableContentMetadata
      __typename
    }
    ... on Exercise {
      exerciseLength
      isSkillCheck
      sponsored
      thumbnailUrl
      timeEstimate {
        lowerBound
        upperBound
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}"""


@dataclass(frozen=True)
class VideoCandidate:
    unit_index: int
    unit_title: str
    lesson_index: int
    lesson_title: str
    video_index: int
    title: str
    path: str
    content_kind: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Khan Academy Grammar video transcripts to CSV."
    )
    parser.add_argument(
        "--course-url",
        default="https://www.khanacademy.org/humanities/grammar",
        help="Khan Academy course URL or path.",
    )
    parser.add_argument(
        "--output",
        default="khan_grammar_transcripts.csv",
        help="CSV output path.",
    )
    parser.add_argument(
        "--transcript-dir",
        default="transcripts",
        help="Directory for individual transcript .txt files.",
    )
    parser.add_argument(
        "--no-text-files",
        action="store_true",
        help="Only write the CSV; do not write individual .txt files.",
    )
    parser.add_argument(
        "--country-code",
        default="US",
        help="Country code sent to Khan Academy contentRoute queries.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Delay in seconds between video transcript requests.",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        default=None,
        help="Optional limit for testing.",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Include videos even when no transcript/subtitles are returned.",
    )
    parser.add_argument(
        "--cookie",
        default=None,
        help="Optional Cookie header copied from a browser session.",
    )
    return parser.parse_args()


def graphql_post(
    operation_name: str,
    query: str,
    variables: dict[str, Any],
    cookie: str | None = None,
    retries: int = 3,
) -> dict[str, Any]:
    endpoint = f"{KHAN_BASE_URL}/api/internal/graphql/{operation_name}"
    payload = json.dumps(
        {
            "operationName": operation_name,
            "variables": variables,
            "query": query,
        }
    ).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (compatible; KA transcript scraper; "
            "+https://www.khanacademy.org/)"
        ),
    }
    if cookie:
        headers["Cookie"] = cookie

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            print(f"Calling: {endpoint}")
            with urlopen(request, timeout=45) as response:
                body = response.read().decode("utf-8")
            data = json.loads(body)
            if data.get("errors"):
                messages = "; ".join(
                    error.get("message", str(error)) for error in data["errors"]
                )
                raise RuntimeError(f"GraphQL error for {operation_name}: {messages}")
            return data
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(
                f"HTTP {exc.code} from {endpoint}: {detail[:500]}"
            )
        except (URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc

        if attempt < retries:
            time.sleep(1.5 * attempt)

    raise RuntimeError(f"Failed {operation_name}: {last_error}") from last_error


def path_from_url(url_or_path: str) -> str:
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        parsed = urlparse(url_or_path)
        path = parsed.path
    else:
        path = url_or_path
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def full_url(path: str) -> str:
    return urljoin(KHAN_BASE_URL, path)


def content_route_payload(route: dict[str, Any]) -> dict[str, Any]:
    content_route = route["data"]["contentRoute"]
    path_data = content_route.get("listedPathData") or content_route.get(
        "unlistedPathData"
    )
    if not path_data:
        raise RuntimeError("Khan Academy did not return listed or unlisted path data.")
    return path_data


def iter_video_candidates(course_data: dict[str, Any]) -> list[VideoCandidate]:
    path_data = content_route_payload(course_data)
    course = path_data.get("course")
    if not course:
        raise RuntimeError("Course data was not present in Khan Academy response.")

    candidates: list[VideoCandidate] = []
    seen_paths: set[str] = set()

    for unit_index, unit in enumerate(course.get("unitChildren") or [], start=1):
        unit_title = unit.get("translatedTitle") or unit.get("slug") or ""
        lesson_index = 0
        for child in unit.get("allOrderedChildren") or []:
            if child.get("__typename") != "Lesson":
                continue
            lesson_index += 1
            lesson_title = child.get("translatedTitle") or child.get("slug") or ""
            video_index = 0
            for item in child.get("curatedChildren") or []:
                kind = item.get("contentKind") or item.get("__typename") or ""
                if kind.lower() not in {"video", "talkthrough"}:
                    continue
                path = (
                    item.get("canonicalUrl")
                    or item.get("urlWithinCurationNode")
                    or item.get("relativeUrl")
                )
                if not path:
                    continue
                path = path_from_url(path)
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                video_index += 1
                candidates.append(
                    VideoCandidate(
                        unit_index=unit_index,
                        unit_title=unit_title,
                        lesson_index=lesson_index,
                        lesson_title=lesson_title,
                        video_index=video_index,
                        title=item.get("translatedTitle") or item.get("slug") or "",
                        path=path,
                        content_kind=kind,
                    )
                )
    return candidates


def fetch_course(course_path: str, country_code: str, cookie: str | None) -> dict[str, Any]:
    return graphql_post(
        "ContentRouteCourseData",
        CONTENT_ROUTE_COURSE_DATA_QUERY,
        {"path": course_path, "countryCode": country_code},
        cookie=cookie,
    )


def fetch_content(path: str, country_code: str, cookie: str | None) -> dict[str, Any]:
    data = graphql_post(
        "ContentRouteLessonAndContentData",
        CONTENT_ROUTE_LESSON_AND_CONTENT_DATA_QUERY,
        {"path": path, "countryCode": country_code},
        cookie=cookie,
    )
    path_data = content_route_payload(data)
    content = path_data.get("content")
    if not content:
        raise RuntimeError(f"No content data returned for {path}")
    return content


def clean_subtitle_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def transcript_from_content(content: dict[str, Any]) -> str:
    subtitles = content.get("subtitles") or []
    texts = [
        clean_subtitle_text(subtitle.get("text", ""))
        for subtitle in subtitles
        if subtitle.get("text")
    ]
    if texts:
        return " ".join(texts)

    augmented = content.get("augmentedTranscript")
    if isinstance(augmented, str):
        return clean_subtitle_text(augmented)
    return ""


def timestamped_transcript_from_content(content: dict[str, Any]) -> str:
    subtitles = content.get("subtitles") or []
    lines: list[str] = []
    for subtitle in subtitles:
        text = clean_subtitle_text(subtitle.get("text", ""))
        if not text:
            continue
        start = subtitle.get("startTime")
        end = subtitle.get("endTime")
        if start is None or end is None:
            lines.append(text)
        else:
            lines.append(f"[{format_seconds(start)} - {format_seconds(end)}] {text}")
    return "\n".join(lines)


def format_seconds(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return str(value)
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def safe_filename(text: str, fallback: str) -> str:
    text = text.strip() or fallback
    text = re.sub(r"[^\w\s.-]", "", text)
    text = re.sub(r"\s+", "-", text)
    return text[:120].strip(".-") or fallback


def write_text_file(
    transcript_dir: Path,
    candidate: VideoCandidate,
    content: dict[str, Any],
    plain_transcript: str,
) -> Path:
    unit = f"unit-{candidate.unit_index:02d}"
    lesson = f"lesson-{candidate.lesson_index:02d}"
    name = safe_filename(candidate.title, f"video-{candidate.video_index:02d}")
    path = transcript_dir / unit / f"{lesson}-{candidate.video_index:02d}-{name}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)

    timestamped = timestamped_transcript_from_content(content)
    body = [
        f"Title: {candidate.title}",
        f"Unit: {candidate.unit_title}",
        f"Lesson: {candidate.lesson_title}",
        f"URL: {full_url(candidate.path)}",
        "",
        "Transcript:",
        plain_transcript,
    ]
    if timestamped:
        body.extend(["", "Timestamped transcript:", timestamped])
    path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
    return path


def scrape(args: argparse.Namespace) -> list[dict[str, Any]]:
    course_path = path_from_url(args.course_url)
    course_data = fetch_course(course_path, args.country_code, args.cookie)
    candidates = iter_video_candidates(course_data)
    if args.max_videos is not None:
        candidates = candidates[: args.max_videos]

    print(f"Found {len(candidates)} video candidates.")
    rows: list[dict[str, Any]] = []
    transcript_dir = Path(args.transcript_dir)

    for index, candidate in enumerate(candidates, start=1):
        print(f"[{index}/{len(candidates)}] {candidate.title}")
        content = fetch_content(candidate.path, args.country_code, args.cookie)
        transcript = transcript_from_content(content)
        if not transcript and not args.include_empty:
            print("  No transcript returned; skipped.")
            continue

        transcript_file = ""
        if not args.no_text_files:
            transcript_file = str(
                write_text_file(transcript_dir, candidate, content, transcript)
            )

        rows.append(
            {
                "unit_index": candidate.unit_index,
                "unit_title": candidate.unit_title,
                "lesson_index": candidate.lesson_index,
                "lesson_title": candidate.lesson_title,
                "video_index": candidate.video_index,
                "video_title": content.get("translatedTitle") or candidate.title,
                "video_url": full_url(candidate.path),
                "content_kind": content.get("contentKind") or candidate.content_kind,
                "youtube_id": content.get("youtubeId") or content.get("translatedYoutubeId") or "",
                "duration_seconds": content.get("duration") or "",
                "transcript_file": transcript_file,
                "transcript": transcript,
            }
        )

        if args.delay:
            time.sleep(args.delay)

    return rows


def write_csv(output_path: Path, rows: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "unit_index",
        "unit_title",
        "lesson_index",
        "lesson_title",
        "video_index",
        "video_title",
        "video_url",
        "content_kind",
        "youtube_id",
        "duration_seconds",
        "transcript_file",
        "transcript",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    try:
        rows = scrape(args)
        write_csv(Path(args.output), rows)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Wrote {len(rows)} transcript rows to {args.output}")
    if not args.no_text_files:
        print(f"Wrote individual transcript files under {args.transcript_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
