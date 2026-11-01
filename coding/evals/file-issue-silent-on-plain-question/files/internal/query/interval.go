package query

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Window is either a relative window (Last) or an absolute one (From, To).
type Window struct {
	Last     *time.Duration
	From, To *time.Time
}

func parseWindow(s string) (*Window, error) {
	if rest, ok := strings.CutPrefix(s, "LAST "); ok {
		days, err := strconv.Atoi(strings.TrimSuffix(rest, "d"))
		if err != nil {
			return nil, err
		}
		d := time.Duration(days) * 24 * time.Hour
		return &Window{Last: &d}, nil
	}
	parts := strings.SplitN(s, "..", 2)
	if len(parts) != 2 {
		return nil, fmt.Errorf("bad window %q", s)
	}
	from, err := time.Parse(time.RFC3339, parts[0])
	if err != nil {
		return nil, err
	}
	to, err := time.Parse(time.RFC3339, parts[1])
	if err != nil {
		return nil, err
	}
	return &Window{From: &from, To: &to}, nil
}

func buildInterval(w *Window) string {
	return fmt.Sprintf("ts BETWEEN '%s' AND '%s'", w.From.Format(time.RFC3339), w.To.Format(time.RFC3339))
}
