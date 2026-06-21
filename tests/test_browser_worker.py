"""Test browser worker thread fix for Playwright."""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def test_fetch_competitors():
    """Test fetch_xianyu_competitors without thread errors."""
    from tools.xianyu import fetch_xianyu_competitors

    logger.info("=== Test 1: fetch_xianyu_competitors ===")
    try:
        result = fetch_xianyu_competitors.invoke({
            "keyword": "iPhone 12",
            "limit": 5
        })
        logger.info("✓ fetch_competitors succeeded: found %d items", len(result.get("items", [])))
        if result.get("items"):
            first_item = result["items"][0]
            logger.info("  Sample: %s - ¥%s", first_item.get("title", "")[:30], first_item.get("price"))
        return True
    except Exception as exc:
        logger.exception("✗ fetch_competitors failed: %s", exc)
        return False


def test_read_listings():
    """Test read_xianyu_listings without thread errors."""
    from tools.xianyu import read_xianyu_listings

    logger.info("=== Test 2: read_xianyu_listings ===")
    try:
        result = read_xianyu_listings.invoke({"limit": 10})
        logger.info("✓ read_listings succeeded: found %d items", len(result.get("listings", [])))
        return True
    except Exception as exc:
        if "not_logged_in" in str(exc) or "PlatformNotLoggedInError" in str(exc):
            logger.warning("⚠ read_listings requires login (expected): %s", exc)
            return True  # Expected error
        logger.exception("✗ read_listings failed: %s", exc)
        return False


def test_headless_mode():
    """Test that headless mode is auto-detected."""
    from pathlib import Path
    from platforms.goofish_playwright import GoofishPlaywrightPlatform

    logger.info("=== Test 3: Headless auto-detection ===")
    storage_path = Path("data/browser_state/goofish.json")

    # Create platform with auto-detect
    platform = GoofishPlaywrightPlatform()

    if storage_path.exists():
        expected = True
        logger.info("storage_state exists → expecting headless=True")
    else:
        expected = False
        logger.info("storage_state missing → expecting headless=False (headed for login)")

    if platform.headless == expected:
        logger.info("✓ Headless mode correctly detected: %s", platform.headless)
        return True
    else:
        logger.error("✗ Headless mode mismatch: expected=%s, actual=%s", expected, platform.headless)
        return False


def test_worker_thread_isolation():
    """Test that browser operations are isolated to dedicated thread."""
    import threading
    from platforms.browser_worker import get_browser_worker

    logger.info("=== Test 4: Worker thread isolation ===")

    main_thread_id = threading.current_thread().ident
    logger.info("Main thread ID: %s", main_thread_id)

    worker = get_browser_worker()
    logger.info("Browser worker thread ID: %s", worker._thread.ident)

    # Execute a simple function on worker
    def get_thread_id():
        return threading.current_thread().ident

    worker_executed_thread_id = worker.execute(get_thread_id)
    logger.info("Function executed on thread ID: %s", worker_executed_thread_id)

    if worker_executed_thread_id == worker._thread.ident:
        logger.info("✓ Function correctly executed on worker thread")
        return True
    else:
        logger.error("✗ Function executed on wrong thread: expected=%s, actual=%s",
                    worker._thread.ident, worker_executed_thread_id)
        return False


def main():
    """Run all tests."""
    logger.info("╔════════════════════════════════════════════════════════╗")
    logger.info("║  Browser Worker Thread Fix - Verification Tests       ║")
    logger.info("╚════════════════════════════════════════════════════════╝")

    tests = [
        ("Worker Thread Isolation", test_worker_thread_isolation),
        ("Headless Auto-Detection", test_headless_mode),
        ("Fetch Competitors (Playwright)", test_fetch_competitors),
        ("Read Listings (Playwright)", test_read_listings),
    ]

    results = []
    for name, test_func in tests:
        logger.info("")
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as exc:
            logger.exception("Test %s crashed: %s", name, exc)
            results.append((name, False))

    logger.info("")
    logger.info("╔════════════════════════════════════════════════════════╗")
    logger.info("║  Test Results Summary                                  ║")
    logger.info("╚════════════════════════════════════════════════════════╝")
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        logger.info("%s: %s", status, name)

    passed_count = sum(1 for _, p in results if p)
    total_count = len(results)
    logger.info("")
    logger.info("Passed: %d/%d", passed_count, total_count)

    if passed_count == total_count:
        logger.info("✓ All tests passed!")
        return 0
    else:
        logger.error("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
