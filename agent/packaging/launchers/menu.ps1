# ============================================================
#  secdogie-agent launcher menu -- a liquid-glass (acrylic)
#  selection window shown by open.bat before launching.
#
#  It prints ONE line to stdout: the secdogie-agent argument
#  string for the chosen action (e.g. "--gui --dry-run"), or
#  nothing at all if the window is closed/cancelled. open.bat
#  captures that line and runs secdogie-agent.exe with it.
#
#  Pure UI: it launches nothing itself, so the .bat stays the
#  single place that runs the program. Requires -STA (WPF).
# ============================================================

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

# --- Windows 10/11 acrylic blur + rounded corners (best-effort) --------------
# The "glass" comes from the OS compositor via SetWindowCompositionAttribute
# (acrylic blur-behind, 1803+) plus DWM rounded corners (Windows 11). Both are
# wrapped so that on an older build where they no-op, the window still shows as
# a translucent dark rounded panel -- the look degrades, it never fails.
$glass = @"
using System;
using System.Runtime.InteropServices;
public static class Glass {
    [StructLayout(LayoutKind.Sequential)]
    struct AccentPolicy { public int AccentState; public int Flags; public int GradientColor; public int AnimationId; }
    [StructLayout(LayoutKind.Sequential)]
    struct WinCompAttrData { public int Attribute; public IntPtr Data; public int SizeOfData; }
    [DllImport("user32.dll")]
    static extern int SetWindowCompositionAttribute(IntPtr hwnd, ref WinCompAttrData data);
    [DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
    public static void Apply(IntPtr hwnd) {
        var accent = new AccentPolicy();
        accent.AccentState = 4;                              // ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.GradientColor = unchecked((int)0x99281C1C);   // 0xAABBGGRR: ~60% dark tint
        int size = Marshal.SizeOf(accent);
        IntPtr ptr = Marshal.AllocHGlobal(size);
        Marshal.StructureToPtr(accent, ptr, false);
        var data = new WinCompAttrData();
        data.Attribute = 19;                                 // WCA_ACCENT_POLICY
        data.Data = ptr; data.SizeOfData = size;
        SetWindowCompositionAttribute(hwnd, ref data);
        Marshal.FreeHGlobal(ptr);
        int round = 2;                                       // DWMWCP_ROUND
        DwmSetWindowAttribute(hwnd, 33, ref round, 4);       // DWMWA_WINDOW_CORNER_PREFERENCE
    }
}
"@
try { Add-Type -TypeDefinition $glass -ErrorAction Stop } catch { }

# --- the window ---------------------------------------------------------------
[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="secdogie-agent" Width="460" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" WindowStyle="None"
        AllowsTransparency="True" Background="Transparent" ResizeMode="NoResize"
        FontFamily="Segoe UI, Microsoft YaHei UI">
  <Window.Resources>
    <Style x:Key="Card" TargetType="Button">
      <Setter Property="Margin" Value="0,0,0,10"/>
      <Setter Property="Padding" Value="16,12"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="face" CornerRadius="12" Background="#1AFFFFFF"
                    BorderBrush="#2EFFFFFF" BorderThickness="1"
                    Padding="{TemplateBinding Padding}">
              <ContentPresenter/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="face" Property="Background" Value="#33FFFFFF"/>
                <Setter TargetName="face" Property="BorderBrush" Value="#7AFFFFFF"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter TargetName="face" Property="Background" Value="#22FFFFFF"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Border x:Name="Root" CornerRadius="16" Padding="22" BorderThickness="1">
    <Border.Background>
      <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
        <GradientStop Color="#26FFFFFF" Offset="0"/>
        <GradientStop Color="#0AFFFFFF" Offset="1"/>
      </LinearGradientBrush>
    </Border.Background>
    <Border.BorderBrush>
      <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
        <GradientStop Color="#59FFFFFF" Offset="0"/>
        <GradientStop Color="#1FFFFFFF" Offset="1"/>
      </LinearGradientBrush>
    </Border.BorderBrush>
    <StackPanel>
      <DockPanel LastChildFill="False" Margin="0,0,0,2">
        <TextBlock Text="secdogie-agent" DockPanel.Dock="Left" Foreground="White"
                   FontSize="20" FontWeight="SemiBold"/>
        <Button x:Name="CloseBtn" DockPanel.Dock="Right" Content="&#x2715;"
                Width="30" Height="30" Foreground="#DDFFFFFF" Background="Transparent"
                BorderThickness="0" FontSize="14" Cursor="Hand"/>
      </DockPanel>
      <TextBlock Text="What should it do?" Foreground="#B8FFFFFF" FontSize="13"
                 Margin="0,0,0,18"/>

      <StackPanel x:Name="Choices">
        <Button Style="{StaticResource Card}" Tag="--gui">
          <StackPanel>
            <TextBlock Text="Describe a task" FontSize="15" FontWeight="SemiBold" Foreground="White"/>
            <TextBlock Text="Type what you want done; it asks before every step." FontSize="12"
                       Foreground="#AAFFFFFF" Margin="0,3,0,0" TextWrapping="Wrap"/>
          </StackPanel>
        </Button>
        <Button Style="{StaticResource Card}" Tag="--gui --dry-run">
          <StackPanel>
            <TextBlock Text="Preview first (dry run)" FontSize="15" FontWeight="SemiBold" Foreground="White"/>
            <TextBlock Text="See what it would do -- touches nothing on your machine." FontSize="12"
                       Foreground="#AAFFFFFF" Margin="0,3,0,0" TextWrapping="Wrap"/>
          </StackPanel>
        </Button>
        <Button Style="{StaticResource Card}" Tag="--gui --desktop-ax">
          <StackPanel>
            <TextBlock Text="Element mode (accessibility)" FontSize="15" FontWeight="SemiBold" Foreground="White"/>
            <TextBlock Text="Clicks UI elements by identity, not by pixel -- steadier on real apps." FontSize="12"
                       Foreground="#AAFFFFFF" Margin="0,3,0,0" TextWrapping="Wrap"/>
          </StackPanel>
        </Button>
        <Button Style="{StaticResource Card}" Tag="--gui --auto">
          <StackPanel>
            <TextBlock Text="Unattended (careful)" FontSize="15" FontWeight="SemiBold" Foreground="White"/>
            <TextBlock Text="No per-step confirmation. High-risk actions still ask." FontSize="12"
                       Foreground="#AAFFFFFF" Margin="0,3,0,0" TextWrapping="Wrap"/>
          </StackPanel>
        </Button>
        <Button Style="{StaticResource Card}" Tag="--init-config">
          <StackPanel>
            <TextBlock Text="Set up / edit API key" FontSize="15" FontWeight="SemiBold" Foreground="White"/>
            <TextBlock Text="Create or locate the config file to paste your key into." FontSize="12"
                       Foreground="#AAFFFFFF" Margin="0,3,0,0" TextWrapping="Wrap"/>
          </StackPanel>
        </Button>
      </StackPanel>
    </StackPanel>
  </Border>
</Window>
"@

$script:choice = $null

try {
    $reader = New-Object System.Xml.XmlNodeReader $xaml
    $window = [Windows.Markup.XamlReader]::Load($reader)
    $script:window = $window

    # Apply the glass once the HWND exists.
    $window.Add_SourceInitialized({
        try {
            $h = (New-Object System.Windows.Interop.WindowInteropHelper $script:window).Handle
            [Glass]::Apply($h)
        } catch { }
    })

    # Borderless window: let the header area drag it.
    $root = $window.FindName('Root')
    $root.Add_MouseLeftButtonDown({ try { $script:window.DragMove() } catch { } })

    # Wire each choice card: record its Tag as the result, then close.
    $choices = $window.FindName('Choices')
    foreach ($btn in $choices.Children) {
        if ($btn -is [System.Windows.Controls.Button]) {
            $btn.Add_Click({ param($s, $e) $script:choice = $s.Tag; $script:window.Close() })
        }
    }
    $window.FindName('CloseBtn').Add_Click({ $script:choice = $null; $script:window.Close() })

    # Esc cancels.
    $window.Add_KeyDown({ param($s, $e) if ($e.Key -eq 'Escape') { $script:choice = $null; $script:window.Close() } })

    $null = $window.ShowDialog()

    # The one and only line of stdout: the chosen arguments (empty on cancel,
    # so open.bat exits quietly instead of launching).
    if ($script:choice) { [Console]::Out.Write($script:choice) }
} catch {
    # The window couldn't be built/shown (no WPF, headless session, ...). Don't
    # leave the user with a launcher that does nothing -- fall back to the GUI.
    [Console]::Out.Write('--gui')
}
