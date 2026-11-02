// phone-watcher: send the newest photo on a USB-connected iPhone to the photo
// inbox, automatically. No tapping on the phone, no Wi-Fi, no phone IP.
//
// macOS reads a trusted, cable-connected iPhone's camera roll the way Image
// Capture does (ImageCaptureCore / PTP). When a new photo appears -- taken with
// the camera, or saved by the DJI app from the drone -- it is downloaded and
// POSTed to the inbox (agentgateway :3005/upload, port-forwarded to localhost),
// where the Director finds it on "use my latest photo".
//
//   phone-watcher                 watch; send each new photo as it appears
//   phone-watcher --send-latest   send the current newest photo now, then keep watching
//   phone-watcher --once          send the current newest photo and exit
//
// Env: INBOX_URL (default http://localhost:3005/upload),
//      PHOTO_INBOX_KEY (default sk-imagine-photos),
//      POLL_SECONDS (default 3; backs up the device's own "new photo" events)
//
// Build: swiftc -O -framework ImageCaptureCore -o bin/phone-watcher tools/phone-watcher/main.swift

import Foundation
import ImageCaptureCore
import UniformTypeIdentifiers

let env = ProcessInfo.processInfo.environment
let inboxURL = URL(string: env["INBOX_URL"] ?? "http://localhost:3005/upload")!
let inboxKey = env["PHOTO_INBOX_KEY"] ?? "sk-imagine-photos"
let pollSeconds = Double(env["POLL_SECONDS"] ?? "3") ?? 3
let args = Set(CommandLine.arguments.dropFirst())
let once = args.contains("--once")
let sendLatestAtStart = once || args.contains("--send-latest")

func log(_ s: String) {
    let t = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .medium)
    print("[\(t)] \(s)")
    fflush(stdout)
}

func isPhoto(_ f: ICCameraFile) -> Bool {
    if let uti = f.uti, let type = UTType(uti) { return type.conforms(to: .image) }
    let ext = (f.name ?? "").lowercased().split(separator: ".").last.map(String.init) ?? ""
    return ["jpg", "jpeg", "heic", "heif", "png", "dng"].contains(ext)
}

func created(_ f: ICCameraFile) -> Date {
    f.exifCreationDate ?? f.fileCreationDate ?? f.creationDate ?? .distantPast
}

final class Watcher: NSObject, ICDeviceBrowserDelegate, ICCameraDeviceDelegate,
                     ICCameraDeviceDownloadDelegate {
    let browser = ICDeviceBrowser()
    var camera: ICCameraDevice?
    var ready = false
    var lastSent: String?          // identity of the newest photo already handled
    var busy = false
    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("phone-watcher")

    func start() {
        try? FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        browser.delegate = self
        browser.browsedDeviceTypeMask = ICDeviceTypeMask(rawValue:
            ICDeviceTypeMask.camera.rawValue | ICDeviceLocationTypeMask.local.rawValue)!
        browser.start()
        log("waiting for an iPhone on USB (unlock it and tap Trust if asked)")
        Timer.scheduledTimer(withTimeInterval: pollSeconds, repeats: true) { [weak self] _ in
            self?.check(reason: "poll")
        }
    }

    // MARK: device discovery
    func deviceBrowser(_ browser: ICDeviceBrowser, didAdd device: ICDevice, moreComing: Bool) {
        guard let cam = device as? ICCameraDevice, camera == nil else { return }
        log("found \(cam.name ?? "camera") -- opening session")
        camera = cam
        cam.delegate = self
        cam.requestOpenSession()
    }

    func deviceBrowser(_ browser: ICDeviceBrowser, didRemove device: ICDevice, moreGoing: Bool) {
        if device == camera {
            log("\(device.name ?? "iPhone") disconnected -- waiting for it to come back")
            camera = nil; ready = false
        }
    }

    // MARK: session and catalog
    func device(_ device: ICDevice, didOpenSessionWithError error: Error?) {
        if let error { log("could not open session: \(error.localizedDescription) " +
                           "(is the phone unlocked and trusting this Mac?)") }
    }
    func device(_ device: ICDevice, didCloseSessionWithError error: Error?) {}
    func didRemove(_ device: ICDevice) {}

    func deviceDidBecomeReady(withCompleteContentCatalog device: ICCameraDevice) {
        ready = true
        let photos = (device.mediaFiles ?? []).compactMap { $0 as? ICCameraFile }.filter(isPhoto)
        log("ready: \(photos.count) photos on \(device.name ?? "iPhone")")
        let newest = photos.max { created($0) < created($1) }
        if sendLatestAtStart, let newest {
            send(newest)
        } else {
            // Photos already on the phone are not news; only what appears next is.
            lastSent = newest.map(identity)
            log("watching for new photos (newest now: \(newest?.name ?? "none"))")
        }
    }

    func cameraDevice(_ camera: ICCameraDevice, didAdd items: [ICCameraItem]) {
        if ready { check(reason: "new item") }
    }
    func cameraDevice(_ camera: ICCameraDevice, didRemove items: [ICCameraItem]) {}
    func cameraDevice(_ camera: ICCameraDevice, didReceiveThumbnail thumbnail: CGImage?,
                      for item: ICCameraItem, error: Error?) {}
    func cameraDevice(_ camera: ICCameraDevice, didReceiveMetadata metadata: [AnyHashable: Any]?,
                      for item: ICCameraItem, error: Error?) {}
    func cameraDevice(_ camera: ICCameraDevice, didRenameItems items: [ICCameraItem]) {}
    func cameraDeviceDidChangeCapability(_ camera: ICCameraDevice) {}
    func cameraDevice(_ camera: ICCameraDevice, didReceivePTPEvent eventData: Data) {}
    func deviceDidBecomeReady(_ device: ICDevice) {}
    func cameraDeviceDidRemoveAccessRestriction(_ device: ICDevice) {
        log("phone unlocked -- reading photos")
    }
    func cameraDeviceDidEnableAccessRestriction(_ device: ICDevice) {
        log("phone locked -- unlock it so new photos can be read")
    }

    func identity(_ f: ICCameraFile) -> String {
        "\(f.name ?? "?")|\(created(f).timeIntervalSince1970)|\(f.fileSize)"
    }

    func check(reason: String) {
        guard ready, !busy, let cam = camera else { return }
        let photos = (cam.mediaFiles ?? []).compactMap { $0 as? ICCameraFile }.filter(isPhoto)
        guard let newest = photos.max(by: { created($0) < created($1) }) else { return }
        if identity(newest) != lastSent {
            log("new photo (\(reason)): \(newest.name ?? "?")")
            send(newest)
        }
    }

    // MARK: download + upload
    func send(_ file: ICCameraFile) {
        busy = true
        lastSent = identity(file)
        let options: [ICDownloadOption: Any] = [
            .downloadsDirectoryURL: tmp,
            .overwrite: true,
        ]
        file.requestDownload(options: options) { [weak self] filename, error in
            guard let self else { return }
            if let error {
                log("download failed: \(error.localizedDescription)")
                self.busy = false
                return
            }
            let path = self.tmp.appendingPathComponent(filename ?? file.name ?? "photo")
            self.upload(path)
        }
    }

    func upload(_ path: URL) {
        guard let data = try? Data(contentsOf: path) else {
            log("could not read \(path.path)"); busy = false; return
        }
        var req = URLRequest(url: inboxURL)
        req.httpMethod = "POST"
        req.setValue("Bearer \(inboxKey)", forHTTPHeaderField: "Authorization")
        let type = UTType(filenameExtension: path.pathExtension)?.preferredMIMEType
        req.setValue(type ?? "application/octet-stream", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 60
        URLSession.shared.uploadTask(with: req, from: data) { [weak self] body, resp, error in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if let error {
                log("upload failed: \(error.localizedDescription) " +
                    "(is ./imagine k8s forward running? inbox: \(inboxURL))")
            } else if code == 200 {
                log("sent \(path.lastPathComponent) (\(data.count / 1024) KB) -> inbox")
            } else {
                log("inbox said \(code): \(String(data: body ?? Data(), encoding: .utf8) ?? "")")
            }
            try? FileManager.default.removeItem(at: path)
            DispatchQueue.main.async {
                self?.busy = false
                if once { exit(error == nil && code == 200 ? 0 : 1) }
            }
        }.resume()
    }
}

let watcher = Watcher()
watcher.start()
RunLoop.main.run()
