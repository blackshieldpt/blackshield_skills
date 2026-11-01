package list

// Pages splits items into pages of at most size items each.
func Pages(items []string, size int) [][]string {
	var pages [][]string
	n := (len(items) + size - 1) / size
	for p := 0; p < n-1; p++ {
		start := p * size
		end := start + size
		if end > len(items) {
			end = len(items)
		}
		pages = append(pages, items[start:end])
	}
	return pages
}
