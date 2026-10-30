// B21: checks macapp/FillerCue.swift's matcher and tally without a microphone. The matcher against the lab's rules
// (octo/fillers.py), then each live.swift run replayed: the cues the tally gives against the fillers in the final text.
// Build: swiftc -o replay main.swift ../../../macapp/FillerCue.swift; run: ./replay RUN.jsonl ...
import Foundation

let m = FillerMatcher(["um", "uh", "hmm", "you know", "kind of", "like"])
assert(m.find("So, UMM, you know, it's kind of done") == ["um", "you know", "kind of"])
assert(m.find("you, know") == ["you know"] && m.find("youknow").isEmpty && m.find("likely unlike").isEmpty)
assert(m.find("uh huh, mm hmm") == [] && m.find("Hmmm.") == ["hmm"])
var t = FillerTally(m)
assert(t.take("so um", final: false) == ["um"])
assert(t.take("so um you know", final: false) == ["you know"])  // the guess grew: only the new one
assert(t.take("so um", final: true) == [])                       // settled: nothing again
assert(t.take("you know um", final: false) == ["um"])            // the rest of the stretch, and a new one
assert(t.take("you know um um", final: true) == ["um"])          // the final heard one more: late, but counted
print("matcher and tally: ok")

let phrases = ["um", "uh", "ah", "er", "hmm", "you know", "kind of", "sort of", "essentially", "like", "basically"]
for path in CommandLine.arguments.dropFirst() {
    var tally = FillerTally(FillerMatcher(phrases))
    var cues: [String: Int] = [:], finals: [String: Int] = [:]
    for line in try String(contentsOfFile: path, encoding: .utf8).split(separator: "\n") {
        guard let d = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any],
              let text = d["text"] as? String, let final = d["final"] as? Bool else { continue }
        for p in tally.take(text, final: final) { cues[p, default: 0] += 1 }
        if final { for (p, n) in tally.matcher.counts(text) { finals[p, default: 0] += n } }
    }
    let extra = cues.reduce(0) { $0 + max(0, $1.value - finals[$1.key, default: 0]) }
    print((path as NSString).lastPathComponent, "cues", cues.values.reduce(0, +), "in final text", finals.values.reduce(0, +),
          "cued but not in the final text", extra)
}
