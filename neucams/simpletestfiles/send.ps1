$udpClient = New-Object System.Net.Sockets.UdpClient
$server = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse('127.0.0.1'), 9999)
$data = [System.Text.Encoding]::ASCII.GetBytes('setrun=mysession1')
$udpClient.Send($data, $data.Length, $server) | Out-Null
$udpClient.Close()