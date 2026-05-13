param(
    [Parameter(Mandatory = $true)]
    [string]$Url,

    [string]$OutRoot = '',

    [string]$Slug = ''
)

$ErrorActionPreference = 'Stop'

if (-not $OutRoot) {
    $SkillRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $OutRoot = Join-Path $SkillRoot '03_归档样例'
}

$DesktopUa = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
$IphoneUa = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
$AndroidUa = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36'

function HtmlDecode([string]$Value) {
    if ($null -eq $Value) { return '' }
    return [System.Net.WebUtility]::HtmlDecode($Value)
}

function UrlDecode([string]$Value) {
    if ($null -eq $Value) { return '' }
    return [System.Net.WebUtility]::UrlDecode($Value)
}

function Match-Value([string]$Text, [string]$Pattern) {
    $m = [regex]::Match($Text, $Pattern, [Text.RegularExpressions.RegexOptions]::Singleline)
    if ($m.Success) {
        return (HtmlDecode $m.Groups[1].Value).Trim()
    }
    return ''
}

function Has-ArticleContent([string]$Html) {
    return ($Html -match 'id="js_content"' -or $Html -match 'content_noencode:\s*JsDecode')
}

function Invoke-WeChatHtml([string]$ArticleUrl) {
    $attempts = @(
        @{ Name = 'desktop-chrome'; Headers = @{ 'User-Agent' = $DesktopUa; 'Accept-Language' = 'zh-CN,zh;q=0.9,en;q=0.8' } },
        @{ Name = 'iphone-safari'; Headers = @{ 'User-Agent' = $IphoneUa; 'Accept-Language' = 'zh-CN,zh;q=0.9,en;q=0.8' } },
        @{ Name = 'android-chrome'; Headers = @{ 'User-Agent' = $AndroidUa; 'Accept-Language' = 'zh-CN,zh;q=0.9,en;q=0.8' } },
        @{ Name = 'default'; Headers = @{ 'Accept-Language' = 'zh-CN,zh;q=0.9,en;q=0.8' } }
    )

    $lastError = ''
    foreach ($attempt in $attempts) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $ArticleUrl -Headers $attempt.Headers -MaximumRedirection 5 -TimeoutSec 45
            $html = $response.Content
            if (Has-ArticleContent $html) {
                return [pscustomobject]@{
                    Method = $attempt.Name
                    Status = $response.StatusCode
                    Html = $html
                }
            }
            $lastError = "method=$($attempt.Name), status=$($response.StatusCode), no article content"
        } catch {
            $lastError = "method=$($attempt.Name), error=$($_.Exception.Message)"
        }
    }

    throw "Unable to fetch article content. Last attempt: $lastError"
}

function Decode-JsEscapedHtml([string]$Encoded) {
    $decoded = [regex]::Replace($Encoded, '\\x([0-9a-fA-F]{2})', {
        param($m)
        [char][Convert]::ToInt32($m.Groups[1].Value, 16)
    })
    $decoded = $decoded -replace '\\/', '/'
    return (HtmlDecode $decoded)
}

function Get-ContentHtml([string]$Html) {
    $m = [regex]::Match($Html, '<div[^>]*id="js_content"[^>]*>', [Text.RegularExpressions.RegexOptions]::Singleline)
    if ($m.Success) {
        $pos = $m.Index + $m.Length
        $depth = 1
        $tagRe = [regex]::new('</?div\b[^>]*>', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
        while ($depth -gt 0) {
            $tm = $tagRe.Match($Html, $pos)
            if (-not $tm.Success) { throw 'Unbalanced #js_content div.' }
            if ($tm.Value.StartsWith('</')) { $depth-- } else { $depth++ }
            $pos = $tm.Index + $tm.Length
        }
        return $Html.Substring($m.Index + $m.Length, ($pos - $m.Index - $m.Length) - 6)
    }

    $m2 = [regex]::Match($Html, "content_noencode:\s*JsDecode\('([\s\S]*?)'\)", [Text.RegularExpressions.RegexOptions]::Singleline)
    if ($m2.Success) {
        return Decode-JsEscapedHtml $m2.Groups[1].Value
    }

    throw 'Cannot locate article content in #js_content or content_noencode.'
}

function ConvertTo-SafeName([string]$Value, [int]$MaxLength = 72) {
    $safe = $Value -replace '[\\/:*?"<>|]', ''
    $safe = $safe -replace '\s+', '-'
    $safe = $safe.Trim('-')
    if (-not $safe) { $safe = 'wechat-article' }
    if ($safe.Length -gt $MaxLength) { $safe = $safe.Substring(0, $MaxLength).Trim('-') }
    return $safe
}

function Clean-Text([string]$HtmlFragment) {
    if (-not $HtmlFragment) { return '' }
    $s = $HtmlFragment -replace '<br\s*/?>', "`n"
    $s = $s -replace '<[^>]+>', ''
    $s = HtmlDecode $s
    $lines = @($s -split "`n" | ForEach-Object { ($_ -replace '\s+', ' ').Trim() } | Where-Object { $_ })
    return (($lines -join "`n").Trim())
}

function Normalize-MmbizUrl([string]$UrlValue) {
    $u = HtmlDecode $UrlValue
    if ($u -match '^http://mmbiz\.qpic\.cn/') { $u = $u -replace '^http://', 'https://' }
    return $u
}

function Get-ImageExtension([string]$ImageUrl) {
    $fmt = 'jpg'
    $fmtMatch = [regex]::Match($ImageUrl, 'wx_fmt=([^&]+)')
    if ($fmtMatch.Success) { $fmt = ($fmtMatch.Groups[1].Value -replace '[^a-zA-Z0-9]', '') }
    if ($fmt -eq 'jpeg') { return 'jpeg' }
    if ($fmt -eq 'png') { return 'png' }
    if ($fmt -eq 'gif') { return 'gif' }
    if ($fmt -eq 'webp') { return 'webp' }
    return $fmt
}

function Add-Unique([System.Collections.Generic.List[string]]$List, [string]$Value) {
    if ($Value -and -not $List.Contains($Value)) {
        $List.Add($Value)
    }
}

function Download-Asset([string]$AssetUrl, [string]$OutPath) {
    Invoke-WebRequest -UseBasicParsing -Uri $AssetUrl -Headers @{ 'User-Agent' = $DesktopUa; 'Referer' = 'https://mp.weixin.qq.com/' } -OutFile $OutPath -TimeoutSec 90
}

function Build-StructuredMarkdown(
    [string]$Title,
    [string]$SourceUrl,
    [string]$PublishTime,
    [string]$AccountId,
    [string]$LocalContent
) {
    $md = New-Object System.Collections.Generic.List[string]
    $md.Add("# $Title")
    $md.Add('')

    $tokens = [regex]::Matches(
        $LocalContent,
        '<h[1-6]\b[\s\S]*?</h[1-6]>|<blockquote\b[\s\S]*?</blockquote>|<figure\b[\s\S]*?</figure>|<p\b[\s\S]*?</p>|<span\b[^>]*display:\s*block[\s\S]*?</span>',
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )

    foreach ($tok in $tokens) {
        $frag = $tok.Value

        if ($frag -match 'class="video-card"') {
            $img = ([regex]::Match($frag, '<img\b[^>]*src="([^"]+)"[^>]*>', [Text.RegularExpressions.RegexOptions]::IgnoreCase)).Groups[1].Value
            $link = ([regex]::Match($frag, '<a\b[^>]*href="([^"]+)"[^>]*>', [Text.RegularExpressions.RegexOptions]::IgnoreCase)).Groups[1].Value
            if ($img) {
                $md.Add("![视频封面]($img)")
                $md.Add('')
            }
            if ($link) {
                $md.Add("[打开原视频]($(HtmlDecode $link))")
                $md.Add('')
            }
            continue
        }

        if ($frag -match '^<h([1-6])\b') {
            $level = [int]$Matches[1]
            $text = Clean-Text $frag
            if ($text) {
                $md.Add(('#' * [Math]::Max(2, $level)) + " $text")
                $md.Add('')
            }
            continue
        }

        if ($frag -match '^<blockquote\b') {
            $text = Clean-Text $frag
            if ($text) {
                foreach ($line in ($text -split "`n")) { $md.Add("> $line") }
                $md.Add('')
            }
            continue
        }

        if ($frag -match '^<figure\b') {
            $img = ([regex]::Match($frag, '<img\b[^>]*src="([^"]+)"[^>]*>', [Text.RegularExpressions.RegexOptions]::IgnoreCase)).Groups[1].Value
            $alt = ([regex]::Match($frag, '<img\b[^>]*alt="([^"]*)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase)).Groups[1].Value
            $cap = Clean-Text ([regex]::Match($frag, '<figcaption\b[\s\S]*?</figcaption>', [Text.RegularExpressions.RegexOptions]::IgnoreCase).Value)
            if ($img) {
                $label = if ($alt) { HtmlDecode $alt } elseif ($cap) { $cap } else { '图片' }
                $md.Add("![$label]($img)")
                $md.Add('')
                if ($cap -and $cap -ne $label) {
                    $md.Add("_$cap_")
                    $md.Add('')
                }
                continue
            }
            $text = Clean-Text $frag
            if ($text) {
                $md.Add($text)
                $md.Add('')
            }
            continue
        }

        if ($frag -match '^<span\b') {
            $text = Clean-Text $frag
            if ($text -match '^\d{2}$') {
                $md.Add("## $text")
                $md.Add('')
            } elseif ($text) {
                $md.Add($text)
                $md.Add('')
            }
            continue
        }

        if ($frag -match '^<p\b') {
            $img = ([regex]::Match($frag, '<img\b[^>]*src="([^"]+)"[^>]*>', [Text.RegularExpressions.RegexOptions]::IgnoreCase)).Groups[1].Value
            if ($img) {
                $alt = ([regex]::Match($frag, '<img\b[^>]*alt="([^"]*)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase)).Groups[1].Value
                if (-not $alt) { $alt = '图片' }
                $md.Add("![$(HtmlDecode $alt)]($img)")
                $md.Add('')
                $frag = [regex]::Replace($frag, '<img\b[^>]*>', '', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
            }
            $text = Clean-Text $frag
            if ($text) {
                $md.Add($text)
                $md.Add('')
            }
        }
    }

    return $md
}

function Remove-TrailingPromoBlocks([System.Collections.Generic.List[string]]$Lines) {
    $cutIndex = -1
    $minIndex = [Math]::Floor($Lines.Count * 0.45)
    $markers = @(
        '^[-—_ ]*好文推荐[-—_ ]*$',
        '^[-—_ ]*相关推荐[-—_ ]*$',
        '^©\s*THE\s+END$',
        '^转载请联系',
        '^投稿或寻求报道',
        '^商务合作',
        '^加入社群'
    )

    for ($idx = 0; $idx -lt $Lines.Count; $idx++) {
        if ($idx -lt $minIndex) { continue }
        $line = $Lines[$idx].Trim()
        foreach ($marker in $markers) {
            if ($line -match $marker) {
                $cutIndex = $idx
                break
            }
        }
        if ($cutIndex -ge 0) { break }
    }

    if ($cutIndex -lt 0) {
        return $Lines
    }

    $trimmed = New-Object System.Collections.Generic.List[string]
    for ($idx = 0; $idx -lt $cutIndex; $idx++) {
        $trimmed.Add($Lines[$idx])
    }
    while ($trimmed.Count -gt 0 -and -not $trimmed[$trimmed.Count - 1].Trim()) {
        $trimmed.RemoveAt($trimmed.Count - 1)
    }
    $trimmed.Add('')
    return $trimmed
}

function Remove-UnusedAssets([string]$AssetsDir, [System.Collections.Generic.List[string]]$MarkdownLines) {
    if (-not (Test-Path -LiteralPath $AssetsDir)) { return }
    $markdownText = $MarkdownLines -join "`n"
    Get-ChildItem -LiteralPath $AssetsDir -File | ForEach-Object {
        $assetRef = 'assets/' + $_.Name
        if ($markdownText -notmatch [regex]::Escape($assetRef)) {
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }
}

function ConvertTo-YamlScalar([object]$Value) {
    if ($null -eq $Value) { return '""' }
    if ($Value -is [bool]) {
        if ($Value) { return 'true' }
        return 'false'
    }
    if ($Value -is [int] -or $Value -is [long]) { return [string]$Value }
    return (($Value | ConvertTo-Json -Compress) -replace "`r?`n", '')
}

function Get-Sha256Hex([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return (($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join '')
    } finally {
        $sha.Dispose()
    }
}

function Add-Frontmatter(
    [System.Collections.Generic.List[string]]$BodyLines,
    [System.Collections.Specialized.OrderedDictionary]$Fields
) {
    $md = New-Object System.Collections.Generic.List[string]
    $md.Add('---')
    foreach ($key in $Fields.Keys) {
        $md.Add("$key`: $(ConvertTo-YamlScalar $Fields[$key])")
    }
    $md.Add('---')
    $md.Add('')
    foreach ($line in $BodyLines) {
        $md.Add($line)
    }
    return $md
}

$fetch = Invoke-WeChatHtml $Url
$html = $fetch.Html

$title = Match-Value $html "var msg_title = '([^']*)'"
if (-not $title) { $title = Match-Value $html 'property="og:title" content="([^"]*)"' }
if (-not $title) { $title = 'wechat-article' }

$accountId = Match-Value $html 'var\s+user_name\s*=\s*"([^"]+)"'
$biz = Match-Value $html 'biz\s*=\s*"([^"]+)"'
$mid = Match-Value $html 'var\s+mid\s*=\s*"?([0-9]+)"?'
$idx = Match-Value $html 'var\s+idx\s*=\s*"?([0-9]+)"?'
$sn = Match-Value $html 'sn\s*=\s*"([0-9a-fA-F]+)"'
$ctRaw = Match-Value $html 'var\s+ct\s*=\s*"?([0-9]+)"?'
$publishTime = ''
$datePart = (Get-Date).ToString('yyyy-MM-dd')
if ($ctRaw) {
    $publishTime = [DateTimeOffset]::FromUnixTimeSeconds([int64]$ctRaw).ToOffset([TimeSpan]::FromHours(8)).ToString('yyyy-MM-dd HH:mm:ss zzz')
    $datePart = [DateTimeOffset]::FromUnixTimeSeconds([int64]$ctRaw).ToOffset([TimeSpan]::FromHours(8)).ToString('yyyy-MM-dd')
}

if (-not $Slug) {
    $Slug = "$datePart-$(ConvertTo-SafeName $title 60)"
}

$outDir = Join-Path $OutRoot $Slug
$assetsDir = Join-Path $outDir 'assets'
New-Item -ItemType Directory -Force -Path $assetsDir | Out-Null

$contentHtml = Get-ContentHtml $html
$contentHtml = [regex]::Replace($contentHtml, '<section class="mp_profile_iframe_wrp"[\s\S]*?</section>', '', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
$contentHtml = [regex]::Replace($contentHtml, '<mp-common-profile\b[\s\S]*?</mp-common-profile>', '', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
$contentHtml = [regex]::Replace($contentHtml, '<section\b([^>]*)>\s*(<img\b[^>]*>)\s*</section>', '<figure$1>$2</figure>', [Text.RegularExpressions.RegexOptions]::IgnoreCase)

$imageUrls = New-Object System.Collections.Generic.List[string]
[regex]::Matches($contentHtml, '<img\b[^>]*(?:data-src|src)="([^"]+)"[^>]*>', [Text.RegularExpressions.RegexOptions]::Singleline) | ForEach-Object {
    $u = Normalize-MmbizUrl $_.Groups[1].Value
    if ($u -match '^https://mmbiz\.qpic\.cn/') { Add-Unique $imageUrls $u }
}

$urlMap = @{}
$i = 1
foreach ($imgUrl in $imageUrls) {
    $ext = Get-ImageExtension $imgUrl
    $fileName = ('image-{0:00}.{1}' -f $i, $ext)
    $filePath = Join-Path $assetsDir $fileName
    try {
        Download-Asset $imgUrl $filePath
        $urlMap[$imgUrl] = "assets/$fileName"
    } catch {
        Write-Warning "Failed to download image: $imgUrl ($($_.Exception.Message))"
    }
    $i++
}

$localContent = $contentHtml
foreach ($remote in $urlMap.Keys) {
    $local = $urlMap[$remote]
    $localContent = $localContent.Replace($remote, $local)
    $localContent = $localContent.Replace((HtmlDecode $remote), $local)
    $localContent = $localContent.Replace(([System.Net.WebUtility]::HtmlEncode($remote)), $local)
}
$localContent = [regex]::Replace($localContent, '<img\b([^>]*?)data-src="([^"]+)"([^>]*)>', '<img$1src="$2"$3>', [Text.RegularExpressions.RegexOptions]::IgnoreCase)

$videoRows = New-Object System.Collections.Generic.List[object]
$videoIndex = 1
$videoMatches = @([regex]::Matches($localContent, '<iframe\b[\s\S]*?</iframe>', [Text.RegularExpressions.RegexOptions]::IgnoreCase))
foreach ($vm in $videoMatches) {
    $tag = $vm.Value
    $coverEnc = ([regex]::Match($tag, 'data-cover="([^"]+)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase)).Groups[1].Value
    $src = (HtmlDecode ([regex]::Match($tag, 'data-src="([^"]+)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase).Groups[1].Value))
    $localCover = ''
    if ($coverEnc) {
        $videoCover = Normalize-MmbizUrl (UrlDecode (HtmlDecode $coverEnc))
        $ext = Get-ImageExtension $videoCover
        $fileName = ('video-cover-{0:00}.{1}' -f $videoIndex, $ext)
        $filePath = Join-Path $assetsDir $fileName
        try {
            Download-Asset $videoCover $filePath
            $localCover = "assets/$fileName"
        } catch {
            Write-Warning "Failed to download video cover: $videoCover ($($_.Exception.Message))"
        }
    }

    if ($localCover) {
        $replacement = '<figure class="video-card"><img src="' + $localCover + '" alt="视频封面"><figcaption>视频 ' + $videoIndex + ' · <a href="' + $src + '">打开原视频</a></figcaption></figure>'
    } else {
        $replacement = '<p class="video-card">视频 ' + $videoIndex + '：<a href="' + $src + '">打开原视频</a></p>'
    }
    $localContent = $localContent.Replace($tag, $replacement)
    $videoRows.Add([pscustomobject]@{ Index = $videoIndex; Cover = $localCover; Link = $src }) | Out-Null
    $videoIndex++
}

$bodyMd = Build-StructuredMarkdown $title $Url $publishTime $accountId $localContent
$bodyMd = Remove-TrailingPromoBlocks $bodyMd
Remove-UnusedAssets $assetsDir $bodyMd
$assetCount = (Get-ChildItem -LiteralPath $assetsDir -File | Measure-Object).Count
$bodyText = (($bodyMd | Where-Object { $null -ne $_ }) -join "`n").Trim()
$capturedAt = [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:sszzz')
$contentHash = Get-Sha256Hex $bodyText
$contentChars = $bodyText.Length

$frontmatterFields = [ordered]@{
    source_type = 'wechat_article'
    source_url = $Url
    title = $title
    published = $publishTime
    wechat_account_id = $accountId
    captured_at = $capturedAt
    fetch_method = $fetch.Method
    text_source = '#js_content with content_noencode fallback'
    image_count = $imageUrls.Count
    video_count = $videoRows.Count
    asset_count = $assetCount
    content_chars = $contentChars
    content_sha256 = $contentHash
    status = 'raw'
}
$structuredMd = Add-Frontmatter $bodyMd $frontmatterFields
[System.IO.File]::WriteAllLines((Join-Path $outDir 'article.md'), $structuredMd, [System.Text.UTF8Encoding]::new($false))

$metadata = [ordered]@{
    schema_version = 1
    source_type = 'wechat_article'
    source_url = $Url
    title = $title
    published = $publishTime
    wechat_account_id = $accountId
    biz = $biz
    mid = $mid
    idx = $idx
    sn = $sn
    captured_at = $capturedAt
    fetch_method = $fetch.Method
    text_source = '#js_content with content_noencode fallback'
    image_count = $imageUrls.Count
    video_count = $videoRows.Count
    asset_count = $assetCount
    content_chars = $contentChars
    content_sha256 = $contentHash
    canonical_source = 'article.md'
    files = @('metadata.json', 'article.md', 'assets/')
    notes = @(
        'This archive intentionally omits original HTML, PDF, screenshots, and intermediate files.',
        'Do not treat parameterized long WeChat URLs as a stable fallback.',
        'If video exists, the Markdown keeps a local video cover and the original video link.'
    )
}
[System.IO.File]::WriteAllText(
    (Join-Path $outDir 'metadata.json'),
    (($metadata | ConvertTo-Json -Depth 6) + "`n"),
    [System.Text.UTF8Encoding]::new($false)
)
$oldReadme = Join-Path $outDir 'README.md'
if (Test-Path -LiteralPath $oldReadme) {
    Remove-Item -LiteralPath $oldReadme -Force
}

$oldStructuredArticle = Join-Path $outDir 'article.structured.local.md'
if (Test-Path -LiteralPath $oldStructuredArticle) {
    Remove-Item -LiteralPath $oldStructuredArticle -Force
}

$result = [pscustomobject]@{
    OutDir = $outDir
    Title = $title
    PublishTime = $publishTime
    FetchMethod = $fetch.Method
    ImageCount = $imageUrls.Count
    VideoCount = $videoRows.Count
    AssetCount = $assetCount
    Files = @('metadata.json', 'article.md', 'assets/')
}

$result | ConvertTo-Json -Depth 4
